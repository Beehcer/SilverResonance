"""Checked production integration for the dual-symbol workspace.

Only the two JavaScript assets of embedded panes are adapted. The normal
single-window assets, API routes, session cookie, market feed and shared store
remain unchanged. A changed source anchor fails explicitly before publication.
"""


def adapt_workspace_chart_js(source: str) -> str:
    """Keep click/pointer bookkeeping separate from a real chart gesture."""
    def replace_once(old: str, new: str) -> None:
        nonlocal source
        count = source.count(old)
        if count != 1:
            raise ValueError(f"Workspace chart dual-pane anchor changed ({count} matches): {old[:90]}")
        source = source.replace(old, new, 1)

    pan_start = "this.followLatest=false;this.drag={kind:'pan',origin:point,view:{...this.view},pointerId:event.pointerId};"
    pan_draft = "this.drag={kind:'pan',origin:point,view:{...this.view},originalFollowLatest:this.followLatest,originalViewCustomized:this.viewCustomized,originalAutoPrice:this.autoPrice,pointerId:event.pointerId};"
    replace_once("if(this.tool==='pan'){" + pan_start, "if(this.tool==='pan'){" + pan_draft)
    replace_once("else{this.selected=null;this.selectedAnchor=null;this.callbacks.select?.(null);" + pan_start, "else{this.selected=null;this.selectedAnchor=null;this.callbacks.select?.(null);" + pan_draft)
    replace_once("if(drag.kind==='pan'){const tx=transform(drag.view", "if(drag.kind==='pan'&&(drag.moved||Math.hypot(point.x-drag.origin.x,point.y-drag.origin.y)>=3)){drag.moved=true;this.followLatest=false;const tx=transform(drag.view")
    replace_once("if(drag?.kind==='pan')this.view=drag.view;", "if(drag?.kind==='pan'){this.view=drag.view;this.followLatest=drag.originalFollowLatest;this.viewCustomized=drag.originalViewCustomized;this.autoPrice=drag.originalAutoPrice;this.callbacks.viewChanged?.(this);}")
    # These tools finish their pointer action on down. The editor may receive
    # the later pointerup, so do not leave an apparent drag in the chart.
    replace_once("if(this.tool==='text'){this.openTextEditor(this.toValue(point,false,true),point);return;}", "if(this.tool==='text'){this.openTextEditor(this.toValue(point,false,true),point);this.releasePointer({pointerId:event.pointerId});return;}")
    replace_once("if(this.tool==='horizontal'){this.callbacks.add?.(", "if(this.tool==='horizontal'){this.releasePointer({pointerId:event.pointerId});this.callbacks.add?.(")
    replace_once("if(!this.drag)return;const drag=this.drag;this.drag=null;this.releasePointer(drag);", "if(!this.drag){if(this.capturedPointerId===event.pointerId)this.releasePointer({pointerId:event.pointerId});return;}const drag=this.drag;this.drag=null;this.releasePointer(drag);")
    replace_once("this.releasePointer(drag);this.clearPresence();this.applyPendingMarket();this.render();}", "this.releasePointer(drag||(this.capturedPointerId!=null?{pointerId:this.capturedPointerId}:null));this.clearPresence();this.applyPendingMarket();this.render();}")
    return source


def adapt_workspace_js(source: str) -> str:
    """Adapt embedded panes only, preserving the production storage namespace."""
    def replace_once(old: str, new: str) -> None:
        nonlocal source
        count = source.count(old)
        if count != 1:
            raise ValueError(f"Workspace dual-pane integration anchor changed ({count} matches): {old[:90]}")
        source = source.replace(old, new, 1)

    replace_once("  const state={", "  const WORKSPACE_PANE=new URLSearchParams(location.search).get('pane')==='pane-b'?'pane-b':'pane-a';\n  try{const key=PREFIX+'pane-layout:'+WORKSPACE_PANE;if(localStorage.getItem(key)===null)localStorage.setItem(key,localStorage.getItem(PREFIX+'layout')||JSON.stringify('main'));}catch{/* Keep the original layout fallback if browser storage is unavailable. */}\n\n  const state={")
    replace_once("function storageGet(key,fallback=null){try{", "function storageGet(key,fallback=null){if(key==='layout')key='pane-layout:'+WORKSPACE_PANE;try{")
    replace_once("function storageSet(key,value){try{", "function storageSet(key,value){if(key==='layout')key='pane-layout:'+WORKSPACE_PANE;try{")
    replace_once("window.innerWidth<=900", "(window.parent!==window?window.parent.innerWidth:window.innerWidth)<=900")
    replace_once("function sendPresence(update={},force=false){", "function sendPresence(update={},force=false){\n    if(window.FullWorkspace&&!window.FullWorkspace.isActive())return;")
    replace_once("async function refreshBackground(){", "async function refreshBackground(){\n    if(window.FullWorkspace&&!window.FullWorkspace.isActive())return;")
    market_during_save = r"""
  async function refreshWhileSaving(generation){
    const symbol=state.symbol,current=()=>generation===state.generation&&symbol===state.symbol;
    if(!current()||state.dualMarketRequest?.generation===generation&&state.dualMarketRequest?.symbol===symbol)return;
    const ticket={generation,symbol};state.dualMarketRequest=ticket;
    try{const market=await refreshMarket(symbol);if(current()&&market)applyActiveMarket(market);}
    catch(error){if(current()&&!error.cancelled){if(state.market)applyActiveMarket({...state.market,fresh:false,unavailable:true});state.marketLoadError=error.message;renderLoadState();setUnavailable(error.message);}}
    finally{if(state.dualMarketRequest===ticket)state.dualMarketRequest=null;}
  }
"""
    replace_once("  async function poll(generation=state.generation,opening=null){", market_during_save + "\n  async function poll(generation=state.generation,opening=null){")
    replace_once("if(!state.session?.user||!state.symbol||state.polling||state.saving||state.loading&&!state.planReady)return;", "if(!state.session?.user||!state.symbol||state.polling||state.loading&&!state.planReady)return;\n    if(state.saving){await refreshWhileSaving(generation);return;}")
    replace_once("function renderSyncStatus(){", "function renderSyncStatus(){\n    window.FullWorkspace?.queueState();")
    replace_once("function renderMarket(){", "function renderMarket(){window.FullWorkspace?.queueState();renderFollowState();")
    replace_once("function renderSymbols(){", "function renderSymbols(){window.FullWorkspace?.queueState();")
    replace_once("WorkspaceSymbols.normalizeGroups(storageGet(symbolGroupsKey()),state.symbols,storageGet('favorites',[]),storageGet('symbol-order',[]))", "WorkspaceSymbols.normalizeGroups(storageGet(symbolGroupsKey(),{version:1,default:[],favorites:[]}),state.symbols)")
    replace_once("const selected=storageGet('symbol-group:'+scope,'default');state.symbolGroup=WorkspaceSymbols.GROUPS.some(group=>group.id===selected)?selected:'default';", "const selected=state.symbolGroup||'all';state.symbolGroup=WorkspaceSymbols.GROUPS.some(group=>group.id===selected)?selected:'all';")
    replace_once("const groups=symbolGroups(),group=state.symbolGroup||'default'", "const groups=symbolGroups(),group=state.symbolGroup||'all'")
    replace_once("if(!list.childNodes.length){const empty=el('div',filter?'本组无匹配品种':customSymbolGroup(group)?'分组为空':'暂无品种','quote-loading'),all=el('button','查看全部','quote-empty-action');all.type='button';all.addEventListener('click',()=>{$('symbol-search').value='';chooseSymbolGroup('all');});empty.append(all);list.append(empty);}", "if(!list.childNodes.length)list.append(el('div',filter?'本组无匹配品种':customSymbolGroup(group)?'分组为空':'暂无品种','quote-loading'));")
    replace_once("if(Date.now()<(state.suppressSymbolClickUntil||0))return;switchSymbol(value);", "if(Date.now()<(state.suppressSymbolClickUntil||0))return;if(window.FullWorkspace){window.FullWorkspace.requestSymbol(value);return;}switchSymbol(value);")
    replace_once("$('symbol').addEventListener('change',event=>switchSymbol(event.target.value));", "$('symbol').addEventListener('change',event=>window.FullWorkspace?window.FullWorkspace.requestSymbol(event.target.value):switchSymbol(event.target.value));")
    replace_once("function addAlert(title,detail,key=null){state.alerts.unshift", "function addAlert(title,detail,key=null){state.alerts=storageGet(alertKey(),[]);state.alerts.unshift")
    replace_once("$('share-board').addEventListener('click',async()=>{const url=new URL(location.href);", "$('share-board').addEventListener('click',async()=>{const url=new URL(window.parent!==window?window.parent.location.href:location.href);")
    replace_once("function setDockVisible(open){", "function setDockVisible(open){if(window.FullWorkspace?.isMaximized?.()){window.FullWorkspace.requestDockVisible(open);return;}")
    focus_safety = r"""
  function dualDockWidth(){
    const dock=$('workspace-dock');
    return dock.hidden?(parseFloat(dock.style.width)||parseFloat(getComputedStyle(dock).width)||204):dock.getBoundingClientRect().width;
  }
  function dualTextDraft(){
    return Object.values(charts).find(chart=>chart.textEditor&&!chart.textEditor.hidden&&(chart.textInput?.value||chart.textComposing)||chart.titleEditor&&!chart.titleEditor.hidden&&(chart.titleComposing||chart.titleEditor.value!==chart.titleLastValue));
  }
  function dualFocusBlocker(){
    const text=dualTextDraft();
    if(text)return{message:'这段文本尚未完成，请先添加或取消。',focus:text.textEditor&&!text.textEditor.hidden?text.textInput:text.titleEditor};
    if(Object.values(charts).some(chart=>chart.drag||chart.pinch||chart.capturedPointerId!=null)||state.levelDrag||state.symbolDrag||state.spaceTool)return{message:'当前拖动尚未结束，请先完成或取消。'};
    if(Object.values(charts).some(chart=>chart.path?.length||chart.insertWave||chart.placementPreview)||state.quickTool||state.quickSequence||state.levelPreview)return{message:'当前绘制尚未完成，请先完成或取消。'};
    if(state.planFormDirty||state.objectDraft||state.nodeDraft||state.riskDirty||state.previewDirty||state.dualSidebarComposing)return{message:'当前编辑尚未完成，请先完成或取消。'};
    if(document.querySelector('dialog[open]'))return{message:'请先处理当前弹窗，再切换查看的品种。'};
    return null;
  }
  function dualCanChangeFocus({notify=false}={}){
    const blocked=dualFocusBlocker();
    if(blocked&&notify){notice(blocked.message);blocked.focus?.focus({preventScroll:true});}
    return !blocked;
  }
  function dualCanHideSidebar(side,{notify=false}={}){
    // Search contents remain in the left sidebar's DOM. The right editor must
    // not be blurred/hidden while a form or IME composition is unfinished.
    if(side!=='right')return true;
    const blocked=Boolean(state.planFormDirty||state.objectDraft||state.nodeDraft||state.riskDirty||state.dualSidebarComposing);
    if(blocked&&notify)notice('右侧编辑尚未完成，请先完成或取消，再收起侧栏。');
    return !blocked;
  }
  function dualPrepareFocusSwitch(){
    if(!dualCanChangeFocus({notify:true}))return false;
    // This only changes which existing pane owns the controls. Never finish a
    // drawing, submit a form, change tools, or refit a chart as a side effect.
    closeObjectContext();
    if(!state.viewOnly){saveViews();persistDraft();sendPresence({cursor:null,preview_object:null,object_id:null,editing:false},true);}
    return true;
  }
  function dualEscapeHasPriority(){
    return Boolean(document.querySelector('dialog[open],#layout-picker[open],#members-panel:not([hidden]),#decision-detail:not([hidden]),#object-context-menu:not([hidden])')||E.isTextTarget(document.activeElement)||dualFocusBlocker()||Object.values(charts).some(chart=>chart.tool&&chart.tool!=='select'));
  }
  document.addEventListener?.('compositionstart',event=>{if(event.target.closest?.('#workspace-dock'))state.dualSidebarComposing=event.target;},true);
  document.addEventListener?.('compositionend',event=>{if(state.dualSidebarComposing===event.target)state.dualSidebarComposing=null;},true);
"""
    replace_once("  function cancelActiveTool(){", focus_safety + "\n  function cancelActiveTool(){")
    replace_once(
        "if(event.key!=='Escape'||document.querySelector('#cases-dialog[open]')||!$('object-context-menu').hidden)return;",
        """if(event.key!=='Escape'||document.querySelector('#cases-dialog[open]')||!$('object-context-menu').hidden)return;
    if(window.FullWorkspace?.isMaximized?.()){
      const draft=dualTextDraft();
      if(draft){event.preventDefault();event.stopImmediatePropagation();notice('这段文本尚未完成，请先添加或取消。');(draft.textEditor&&!draft.textEditor.hidden?draft.textInput:draft.titleEditor)?.focus({preventScroll:true});return;}
      if(E.isTextTarget(event.target)||event.isComposing||event.keyCode===229)return;
    }""")
    follow_state = r"""
  function dualViewKey(){return'pane-view-ui:'+state.session?.user?.id+':'+WORKSPACE_PANE+':'+state.symbol;}
  function saveDualViewUI(){
    if(state.viewOnly||state.following||state.loading||!state.symbol||!state.session?.user||state.dualViewScope!==dualViewKey())return;
    const key=dualViewKey(),grid=document.querySelector('.chart-grid'),value={activePeriod:state.active,expanded:grid?.dataset.expanded||null};
    if(!E.equal(storageGet(key),value))storageSet(key,value);
  }
  function restoreDualViewUI(){
    if(state.viewOnly||!state.symbol||!state.session?.user||!marketComplete())return;
    const key=dualViewKey(),saved=storageGet(key,{}),grid=document.querySelector('.chart-grid'),periods=WorkspaceLayouts.get(state.layout).periods;
    state.dualViewScope=key;
    if(periods.includes(saved.expanded))grid.dataset.expanded=saved.expanded;else delete grid.dataset.expanded;
    if(periods.includes(saved.activePeriod))activate(saved.activePeriod);
    saveDualViewUI();renderFollowState();
  }
  function renderFollowState(){
    const button=$('follow-latest'),chart=charts[state.active];if(!button||!chart)return;
    const fixed=chart.followLatest===false||chart.viewCustomized===true||Boolean(state.following);
    button.textContent=fixed?'回到最新':'跟随最新';
    button.title=state.following?'正在跟随伙伴视角，行情仍在更新；点击回到最新':fixed?'视角已固定，行情仍在更新；点击跟随最新':'正在跟随最新行情';
    button.setAttribute('aria-pressed',String(!fixed));
  }
"""
    replace_once("  function syncView(source){", follow_state + "\n  function syncView(source){")
    replace_once("saveViews();sendPresence({period:source.period});}", "saveViews();sendPresence({period:source.period});renderFollowState();}")
    replace_once("function activate(period){if(!charts[period])return;state.active=period;", "function activate(period){if(!charts[period])return;state.active=period;renderFollowState();")
    replace_once("renderQuickPlan();renderWaveTools();if(state.plan)renderObjects();}", "renderQuickPlan();renderWaveTools();if(state.plan)renderObjects();saveDualViewUI();}")
    replace_once("function saveViews(){if(", "function saveViews(){saveDualViewUI();if(")
    replace_once("Object.assign(c,v);c.render();}}}", "Object.assign(c,v);c.render();}}restoreDualViewUI();renderFollowState();}")
    replace_once("if(!state.viewOnly&&!state.following)storageSet('layout',layout.id);}", "if(!state.viewOnly&&!state.following)storageSet('layout',layout.id);saveDualViewUI();}")
    replace_once("state.memberSignature=null;saveViews();}", "state.memberSignature=null;saveViews();renderFollowState();}")
    replace_once("$('follow-latest').addEventListener('click',()=>{charts[state.active].fit();saveViews();});", "$('follow-latest').addEventListener('click',()=>{stopFollowing(false);charts[state.active].fit();saveViews();renderFollowState();});")
    replace_once(
        "menu.style.left=Math.max(8,Math.min(x,innerWidth-bounds.width-8))+'px';menu.style.top=Math.max(8,Math.min(y,innerHeight-bounds.height-8))+'px';",
        """const pane=event.target?.closest?.('#dual-pane-surface,#workspace-dock'),area=pane?.getBoundingClientRect()||{left:0,top:0,right:innerWidth,bottom:innerHeight};
    const availableHeight=Math.max(32,area.bottom-area.top-16);
    menu.style.maxHeight=availableHeight+'px';menu.style.maxWidth=Math.max(32,area.right-area.left-16)+'px';menu.style.overflowY='auto';
    const fitted=menu.getBoundingClientRect();
    menu.style.left=Math.max(area.left+8,Math.min(x,area.right-fitted.width-8))+'px';
    menu.style.top=Math.max(area.top+8,Math.min(y,area.bottom-fitted.height-8))+'px';""")

    bridge = r"""
  window.FullWorkspace?.register({
    canChangeFocus:dualCanChangeFocus,
    canHideSidebar:dualCanHideSidebar,
    prepareFocusSwitch:dualPrepareFocusSwitch,
    escapeHasPriority:dualEscapeHasPriority,
    snapshot:()=>({symbol:state.symbol,name:state.market?.name||state.symbol,board:state.board||'',loading:Boolean(state.loading),dirty:Boolean(state.dirty||state.planFormDirty||state.outbox),saving:Boolean(state.saving),conflict:Boolean(state.conflict),ready:Boolean(state.planReady&&marketComplete()),activePeriod:state.active,layout:state.layout,expanded:document.querySelector('.chart-grid')?.dataset.expanded||null,viewOnly:state.viewOnly,revision:state.revision,selected:state.selected,modalOpen:Boolean(document.querySelector('dialog[open],#layout-picker[open],#members-panel:not([hidden]),#decision-detail:not([hidden])')),ui:{group:state.symbolGroup||'all',search:$('symbol-search').value,panel:state.panel||'quick-plan-panel',dockOpen:!$('workspace-dock').hidden,dockWidth:dualDockWidth()}}),
    switchSymbol:async(symbol,board=null)=>{await switchSymbol(symbol,board,{backgroundLoad:true});return state.symbol===symbol&&(board===null||(state.board||'')===(board||''));},
    prepareDeactivate:async({closing=false}={})=>{
      const unfinishedText=Object.values(charts).find(chart=>chart.textEditor&&!chart.textEditor.hidden&&(chart.textInput?.value||chart.textComposing));
      if(unfinishedText){notice('这段文本尚未添加，请先添加或取消。');unfinishedText.textInput?.focus({preventScroll:true});return false;}
      if(state.viewOnly)return true;
      if(state.planFormDirty)savePlanForm();
      finishDrawings();for(const chart of Object.values(charts))chart.cancelGesture();chooseTool('select');
      state.quickTool=null;state.quickSequence=false;state.levelPreview=null;state.levelDrag=null;state.spaceTool=null;
      closeObjectContext();saveViews();persistDraft();renderQuickPlan();renderDrawingState();
      sendPresence({cursor:null,preview_object:null,object_id:null},true);
      return closing?await ensureSaved():true;
    },
    becameActive:()=>{
      state.symbolGroups=null;state.symbolGroupScope=null;renderSymbols();
      sendPresence({cursor:null,preview_object:null,object_id:null},true);
      charts[state.active]?.host.focus({preventScroll:true});
    },
    becamePassive:()=>{presenceSender.reset();state.localPresence=null;},
    applyUI:ui=>{
      if(!ui)return;
      if(typeof ui.search==='string')$('symbol-search').value=ui.search;
      if(WorkspaceSymbols.GROUPS.some(group=>group.id===ui.group))state.symbolGroup=ui.group;
      if(Number.isFinite(ui.dockWidth)&&ui.dockWidth>=204)$('workspace-dock').style.width=ui.dockWidth+'px';
      if(typeof ui.dockOpen==='boolean')setDockVisible(ui.dockOpen);
      if(PANEL_IDS.includes(ui.panel))openPanel(ui.panel,false);
      renderSymbols();
    },
    beginSidebarDrag:symbol=>{
      if(state.viewOnly||!(state.symbols||[]).some(item=>(item.symbol||item)===symbol))return false;
      state.symbolDrag=symbol;state.symbolDragGroup=state.symbolGroup;state.suppressSymbolClickUntil=Date.now()+350;
      return true;
    },
    sidebarDrop:(symbol,x,y)=>{
      if(state.viewOnly||!state.symbolDrag||state.symbolDrag!==symbol)return false;
      const hit=document.elementFromPoint(x,y),tab=hit?.closest('[data-symbol-group]');
      if(tab&&customSymbolGroup(tab.dataset.symbolGroup)){
        const target=tab.dataset.symbolGroup;
        saveSymbolGroups(WorkspaceSymbols.addToGroup(symbolGroups(),target,symbol),'已加入'+(target==='default'?'默认':'喜爱'));return true;
      }
      const row=hit?.closest('.quote-entry'),target=row?.querySelector('.quote-row')?.dataset.symbol;
      if(row&&target&&customSymbolGroup()&&state.symbolDragGroup===state.symbolGroup&&symbolGroups()[state.symbolGroup].includes(symbol)){
        const bounds=row.getBoundingClientRect();
        saveSymbolGroups(WorkspaceSymbols.moveInGroup(symbolGroups(),state.symbolGroup,symbol,target,y<bounds.top+bounds.height/2),'已调整顺序');return true;
      }
      return false;
    },
    endSidebarDrag:()=>endSymbolDrag(),
    syncStorage:key=>{
      if(key?.startsWith(PREFIX+'symbol-groups:')||key?.startsWith(PREFIX+'symbol-group:')){
        state.symbolGroups=null;state.symbolGroupScope=null;renderSymbols();
      }
    }
  });
"""
    replace_once("  applyViewMode();\n  boot();", bridge + "\n  applyViewMode();\n  boot();")
    return source
