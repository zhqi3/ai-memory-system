"""Portable reader for an explicitly selected public workspace."""
import argparse, hashlib, json, os, re, sys
from pathlib import Path
import importlib.util

# Adapters also import this entry via spec_from_file_location from another cwd.
# Resolve the sibling explicitly so behavior never depends on sys.path contents.
_reader_spec=importlib.util.spec_from_file_location('_startup_navigation_reader',Path(__file__).resolve().with_name('navigation_reader.py'))
_reader=importlib.util.module_from_spec(_reader_spec)
_reader_spec.loader.exec_module(_reader)
NavigationReadError=_reader.NavigationReadError
navigation_snapshot=_reader.navigation_snapshot
vault_lock=_reader.vault_lock

def physical(path, *, kind=None):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(ord(c)<32 for c in str(path)):
        raise ValueError(f'Invalid local absolute path: {path}')
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError(f'Symlink component is forbidden: {part}')
    resolved = path.resolve(strict=True)
    if kind == 'file' and not resolved.is_file(): raise ValueError(f'Not a regular file: {path}')
    if kind == 'dir' and not resolved.is_dir(): raise ValueError(f'Not a directory: {path}')
    return resolved

def contained(root, path, kind='file'):
    result=physical(path,kind=kind)
    if not result.is_relative_to(root): raise ValueError(f'Path escapes canonical memory: {path}')
    return result

def read(root,path): return contained(root,path).read_text(encoding='utf-8-sig')

def resolve(config, project=None, workspace=None, bootstrap=False):
    config=physical(config,kind='file');pointer_bytes=config.read_bytes();data=json.loads(pointer_bytes.decode('utf-8-sig'))
    if data.get('schema_version')!='ai-memory-workspace/1.0' or not isinstance(data.get('memory_root'),str):
        raise ValueError(f'Malformed memory pointer: {config}')
    from workspace import load_workspace
    _,root,_=load_workspace(config.parent)
    if config.name != 'workspace.json':raise ValueError('Use this workspace marker only')
    result={'status':'loaded','memory_root':str(root),'config_path':str(config),'read_only':True}
    try:
        with vault_lock(root):
            return _snapshot(root,config,pointer_bytes,result,project,workspace,bootstrap)
    except NavigationReadError as exc:
        result.update(status=exc.status,message=str(exc),navigation={'status':exc.status,'diagnostics':exc.diagnostics})
        return result
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        # Once a snapshot has started, failure must not leak an earlier partial
        # body. Retrying or explicit recovery happens outside this reader.
        result.update(status='partial',message=str(exc))
        return result

def _snapshot(root,config,pointer_bytes,result,project,workspace,bootstrap):
    captured={config:pointer_bytes}
    def capture(path):
        path=physical(path,kind='file')
        if path not in captured:captured[path]=path.read_bytes()
        return captured[path]
    def text(path):return capture(contained(root,path)).decode('utf-8-sig')
    for d in ['global','projects']:contained(root,root/d,'dir')
    for f in ['AI_MEMORY_CONTRACT.md','global/startup-core.md']:contained(root,root/f)
    if not project and not workspace:return result
    if project=='_template':raise ValueError('_template is not a project')
    if workspace:workspace=physical(workspace,kind='dir')
    matches=[]
    for p in (root/'projects').iterdir():
        if p.name.startswith('.') or p.name=='_template':continue
        contained(root,p,'dir');manifest=p/'project-manifest.json'
        m=json.loads(text(manifest))
        if m.get('schema_version')!='2.0' or m.get('index_file')!='memory-index.md' or not isinstance(m.get('canonical_name'),str):
            raise ValueError(f'Malformed project manifest: {manifest}')
        paths=m.get('workspace_paths',[])
        for x in paths:
            if not isinstance(x,str) or not Path(x).is_absolute() or '..' in Path(x).parts:
                raise ValueError(f'Invalid workspace path in {manifest}: {x}')
        names=[m['canonical_name'],p.name,*m.get('aliases',[])]
        by_name=project and project in names
        by_workspace=not project and workspace and any(workspace==Path(x) or workspace.is_relative_to(Path(x)) for x in paths)
        if by_name or by_workspace:matches.append((p,m))
    if len(matches)!=1:
        raise ValueError(f'Project identity must resolve uniquely; matches={len(matches)}, project={project}, workspace={workspace}')
    p,m=matches[0];files=['project-manifest.json','memory-index.md','handoff.md','working-agreement.md']
    optional_paths=[p/'navigation.json',root/'global/navigation/moss.json']
    absent=[path for path in optional_paths if not path.exists() and not path.is_symlink()]
    navigation,extra=navigation_snapshot(root,p,m['canonical_name'],capture,bootstrap=bootstrap)
    raw_contents={'global/startup-core.md':capture(root/'global/startup-core.md')}
    for f in files:raw_contents[f]=capture(p/f)
    raw_contents.update(extra)
    contents={key:raw.decode('utf-8-sig') for key,raw in raw_contents.items()}
    # Cooperative writers are serialized by the lock; this last comparison also
    # detects ordinary uncoordinated human edits during our read window.
    for path,raw in captured.items():
        if physical(path,kind='file').read_bytes()!=raw:
            raise NavigationReadError(f'File changed during startup snapshot: {path}')
    for path in absent:
        _reader.absolute_path(path)
        if path.exists() or path.is_symlink():
            raise NavigationReadError(f'Navigation configuration appeared during startup snapshot; retry: {path}')
    result.update(canonical_name=m['canonical_name'],project_path=str(p),layout_version=2,files_loaded=list(contents),sha256={k:hashlib.sha256(raw).hexdigest() for k,raw in raw_contents.items()})
    if navigation:
        result['navigation']=navigation
        if not bootstrap and navigation['status']=='inspected':
            result['status']='inspected'
            result['files_inspected']=result.pop('files_loaded')
    if bootstrap:result['contents']=contents
    return result

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--config',type=Path,required=True)
    a.add_argument('--project');a.add_argument('--workspace',type=Path);a.add_argument('--bootstrap',action='store_true')
    args=a.parse_args()
    try:result=resolve(args.config,args.project,args.workspace,args.bootstrap)
    except Exception as e:result={'status':'error','config_path':str(args.config),'message':str(e),'read_only':True}
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['status'] in ('loaded','inspected') else 1

if __name__=='__main__':sys.exit(main())
