(function(root){
  'use strict';
  // No example data, diagrams, or their styles participate in the chart startup.
  const mounted = new WeakMap();
  function resource(source, kind){
    return new Promise((resolve,reject)=>{
      const node=document.createElement(kind==='css'?'link':'script');
      if(kind==='css'){node.rel='stylesheet';node.href=source;}else node.src=source;
      const timer=setTimeout(()=>{node.remove();reject(new Error('案例资源读取超时'));},12000);
      node.onload=()=>{clearTimeout(timer);resolve();};
      node.onerror=()=>{clearTimeout(timer);node.remove();reject(new Error('案例资源暂未加载'));};
      document.head.append(node);
    });
  }
  function mount(options={}){
    const button=options.button;if(!button||mounted.has(button))return mounted.get(button);
    let controller=null,pending=null,wanted=false,placeholder=null;
    function shell(){
      if(placeholder)return;
      placeholder=document.createElement('dialog');placeholder.id='cases-dialog';
      placeholder.setAttribute('aria-label','正在打开案例库');
      placeholder.style.cssText='position:fixed;inset:0;width:100vw;max-width:none;height:100dvh;max-height:none;margin:0;border:0;background:#0c151c;color:#d5e1e9;padding:24px;box-sizing:border-box';
      const close=document.createElement('button');close.type='button';close.textContent='返回盘面 ×';
      close.style.cssText='float:right;color:inherit;background:#1a2834;border:1px solid #425565;padding:8px 14px';
      const title=document.createElement('h2');title.textContent='开仓案例库';
      const status=document.createElement('p');status.setAttribute('role','status');status.textContent='正在加载案例…';
      close.onclick=()=>{wanted=false;placeholder?.close();};
      placeholder.addEventListener('cancel',()=>{wanted=false;});
      placeholder.addEventListener('close',()=>{placeholder?.remove();placeholder=null;});
      placeholder.append(close,title,status);document.body.append(placeholder);placeholder.showModal();close.focus();
    }
    async function load(){
      const template=document.getElementById('cases-resources');
      const css=template?.content.querySelector('link'),scripts=[...template?.content.querySelectorAll('script[src]')||[]];
      if(!css||scripts.length!==2)throw new Error('案例资源配置不完整');
      const sheet=resource(css.href,'css');
      await Promise.all([sheet,root.WorkspaceCasesData?Promise.resolve():resource(scripts[0].src,'js')]);
      if(root.WorkspaceCases===api)await resource(scripts[1].src,'js');
      if(root.WorkspaceCases===api)throw new Error('案例组件尚未就绪');
      const result=root.WorkspaceCases.mount({...options,button:null});
      result.dialog.addEventListener('close',()=>button.focus({preventScroll:true}));
      return result;
    }
    async function open(){
      wanted=true;if(controller){controller.open();return;}shell();
      if(!pending)pending=load().then(value=>controller=value).finally(()=>pending=null);
      try{await pending;const reopen=wanted;if(placeholder){const old=placeholder;placeholder=null;old.remove();}if(reopen)controller.open();}
      catch(error){if(placeholder){placeholder.querySelector('[role=status]').textContent='案例加载较慢，请返回盘面后重试。';}button.title='点击重新加载案例';}
    }
    button.addEventListener('click',open);
    const handle={open,close(){wanted=false;controller?.close();placeholder?.close();},isOpen(){return Boolean(placeholder?.open||controller?.isOpen());}};
    mounted.set(button,handle);return handle;
  }
  const api={mount};root.WorkspaceCases=api;
}(globalThis));
