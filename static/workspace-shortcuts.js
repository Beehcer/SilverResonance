(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceShortcuts=api;}(globalThis,function(){
  'use strict';
  const installed=new WeakMap();

  function shouldPreventSave(event){
    return Boolean(event&&(event.ctrlKey||event.metaKey)&&String(event.key||'').toLowerCase()==='s');
  }

  function allowTextSelection(target){
    const element=target?.nodeType===3?target.parentElement:target;
    if(!element)return false;
    if(element.closest?.('input, textarea, select'))return true;
    if(typeof element.isContentEditable==='boolean')return element.isContentEditable;
    // Also support lightweight hosts without isContentEditable; the closest explicit false is a boundary.
    for(let current=element;current;current=current.parentElement){
      const raw=current.getAttribute?.('contenteditable');if(raw===null||raw===undefined)continue;
      const value=String(raw).trim().toLowerCase();
      if(value==='false')return false;
      if(value===''||value==='true'||value==='plaintext-only')return true;
    }
    return false;
  }

  function install(document){
    if(installed.has(document))return installed.get(document);
    const options={capture:true};
    const keydown=event=>{if(shouldPreventSave(event))event.preventDefault();};
    const selectstart=event=>{const target=event.composedPath?.()[0]||event.target;if(!allowTextSelection(target))event.preventDefault();};
    document.addEventListener('keydown',keydown,options);
    document.addEventListener('selectstart',selectstart,options);
    const uninstall=()=>{if(installed.get(document)!==uninstall)return;document.removeEventListener('keydown',keydown,options);document.removeEventListener('selectstart',selectstart,options);installed.delete(document);};
    installed.set(document,uninstall);
    return uninstall;
  }

  return{install,shouldPreventSave,allowTextSelection};
}));
