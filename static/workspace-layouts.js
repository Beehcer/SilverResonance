(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceLayouts=api;}(globalThis,function(){
  const layouts=[
    {id:'main',name:'30 分主图',periods:['m30','day','m3'],primary:'m30',areas:'"m30 day" "m30 m3"',columns:'2.2fr 1fr',rows:'1fr 1fr'},
    {id:'daily',name:'日线主图',periods:['day','m30','m3'],primary:'day',areas:'"day m30" "day m3"',columns:'2.2fr 1fr',rows:'1fr 1fr'},
    {id:'detail',name:'3 分主图',periods:['m3','day','m30'],primary:'m3',areas:'"m3 day" "m3 m30"',columns:'2.2fr 1fr',rows:'1fr 1fr'},
    {id:'columns',name:'三图横排',periods:['day','m30','m3'],primary:'m30',areas:'"day m30 m3"',columns:'1fr 1fr 1fr',rows:'1fr'},
    {id:'rows',name:'三图竖排',periods:['day','m30','m3'],primary:'m30',areas:'"day" "m30" "m3"',columns:'1fr',rows:'1fr 1fr 1fr'},
    {id:'top',name:'30 分在上',periods:['m30','day','m3'],primary:'m30',areas:'"m30 m30" "day m3"',columns:'1fr 1fr',rows:'1.4fr 1fr'},
    {id:'day30',name:'日线 + 30 分',periods:['day','m30'],primary:'m30',areas:'"day m30"',columns:'1fr 1.6fr',rows:'1fr'},
    {id:'intraday',name:'30 分 + 3 分',periods:['m30','m3'],primary:'m30',areas:'"m30 m3"',columns:'1.6fr 1fr',rows:'1fr'}
  ];
  return{layouts,get:id=>layouts.find(layout=>layout.id===id)||layouts[0]};
}));
