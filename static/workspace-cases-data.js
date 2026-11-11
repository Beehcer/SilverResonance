(function(root,factory){const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.WorkspaceCasesData=value;}(globalThis,function(){
  'use strict';
  const source={title:'期货交易系统：多周期结构与点3确认',sha256:'40a20922580911199dd607f171f53b78744be628a47e729288ed2b570b69f5ed',updatedAt:'2026-11-09T22:44:40+08:00'};
  const families=[
    {id:'continuation',title:['上涨回撤后恢复','下跌反弹后受阻'],mode:'continuation',category:'核心模式',refs:['三·3 延续交易的完整判断链','二·9 点3内部突破与追突破的区别'],feature:'原趋势仍有效',detail:['上涨趋势中的回撤。30分钟先收盘突破回撤内部高点，随后3分钟回撤守住并再转强。','下跌趋势中的反弹。30分钟先收盘跌破反弹内部低点，随后3分钟回抽受阻并再转弱。']},
    {id:'reversal-a',title:['A类：突破后回踩','A类：破位后回抽'],mode:'reversal_a',category:'核心模式',refs:['四 趋势逆转的标准流程','五 A类点3','八·3 从点2破位确认到点3做空候选'],feature:'关键水平已破',detail:['30分钟下降趋势线与预选关键前高均已被突破；点3验证原压力转支撑。','30分钟上升趋势线与预选关键前低均已被跌破；点3验证原支撑转压力。']},
    {id:'reversal-b',title:['B类：旧结构内试多','B类：旧结构内试空'],mode:'reversal_b',category:'核心模式',refs:['五 B类点3','六 B类点3后的压力处理'],feature:'关键水平未破',detail:['下降趋势线已破，已收盘突破局部高点，但30分钟关键前高仍在上方。只按B类弱确认试多。','上升趋势线已破，已收盘跌破局部低点，但30分钟关键前低仍在下方。只按B类弱确认试空。']},
    {id:'range',title:['区间下沿验证做多','区间上沿验证做空'],mode:'range',category:'核心模式',refs:['二·8 区间边缘的具体限制','九 允许入场'],feature:'边缘向区间内部',detail:['区间与上下边界事前已有多次试探依据。下沿出现独立进攻、回撤和再转强，先看最近内部障碍。','区间与上下边界事前已有多次试探依据。上沿出现独立进攻、回抽和再转弱，先看最近内部障碍。']},
    {id:'2b-wick',title:['点3内：刺穿后当根收回','点3内：上刺后当根跌回'],mode:'reversal_a',category:'2B与收回',refs:['术语·本系统采用的识别口径','二·8 2B、假突破收回的具体记录顺序'],feature:'2B · 当根收回',detail:['既有A类主结构点3内，3分钟实际刺穿预选前低后当根收回；随后另有局部进攻、回撤和触发。','既有A类主结构点3内，3分钟实际越过预选前高后当根跌回；随后另有局部进攻、回抽和触发。']},
    {id:'2b-multibar',title:['点3内：破位后再收复','点3内：上破后再跌回'],mode:'reversal_a',category:'2B与收回',refs:['术语·本系统采用的识别口径','二·8 2B、假突破收回的具体记录顺序'],feature:'2B · 后续收回',detail:['先有3分钟收盘破低，再在预设3根期限内收复；保留首次破位记录，收复后另等局部验证。','先有3分钟收盘破高，再在预设3根期限内跌回；保留首次上破记录，跌回后另等局部验证。']},
    {id:'2b-zone',title:['点3内：支撑区域假跌破','点3内：压力区域假上破'],mode:'reversal_a',category:'2B与收回',refs:['术语·本系统采用的识别口径 第5项','二·8 2B、假突破收回的具体记录顺序'],feature:'区域假突破收回',detail:['实际跌出支撑区下沿，首次只回到区域内部仍待定；随后收复上沿，才记录区域收回完成。','实际越过压力区上沿，首次只回到区域内部仍待定；随后跌回下沿，才记录区域收回完成。']},
    {id:'inside-child',title:['内包线：子线高点扳机','内包线：子线低点扳机'],mode:'continuation',category:'包含组合',refs:['二·10 孕线／内包线与入场·子线触发','三·3 延续交易的完整判断链'],feature:'严格内包 · 子线触发',detail:['主周期已许可，严格内包母子线均已收盘。事前选择子线高点触发，成交可以仍在母线范围内。','主周期已许可，严格内包母子线均已收盘。事前选择子线低点触发，成交可以仍在母线范围内。']},
    {id:'inside-outer',title:['内包线：组合上沿扳机','内包线：组合下沿扳机'],mode:'continuation',category:'包含组合',refs:['二·10 孕线／内包线与入场·组合外沿触发','二·10 防守与止损'],feature:'严格内包 · 组合触发',detail:['事前选择组合最高价，不能临时改用子线高点提前入场。突破母线高点时仍在主结构点3允许区。','事前选择组合最低价，不能临时改用子线低点提前入场。跌破母线低点时仍在主结构点3允许区。']},
    {id:'harami',title:['实体孕线：使用真实组合上沿','实体孕线：使用真实组合下沿'],mode:'continuation',category:'包含组合',refs:['二·10 孕线／内包线与入场·先定参考范围','二·10 防守与止损'],feature:'实体孕线 · 影线越母线',detail:['子线实体被母线实体包含，但上影线越过母线最高价，因此不是严格内包；触发须用母子线实际最高价。','子线实体被母线实体包含，但下影线越过母线最低价，因此不是严格内包；触发须用母子线实际最低价。']},
    {id:'defense',title:['对应防守高点上破后再验证','对应防守低点下破后再验证'],mode:'continuation',category:'结构细分',refs:['二·6 推进高点与防守低点要对应','八·4 推进高点对应的防守低点','三·3 延续交易的完整判断链'],feature:'推进与防守点对应',detail:['上涨回撤中的下压段，事前标出与推进低点对应的反弹高点。30分钟收盘上破后再回踩，不仅凭不创新低入场。','下跌反弹中的上攻段，事前标出与推进高点对应的回撤低点。30分钟收盘下破后再反弹，不仅凭不创新高入场。']},
    {id:'failed-expectation',title:['该跌未跌，另验做多结构','该涨未涨，另验做空结构'],mode:'reversal_a',category:'结构细分',refs:['二·11 预期未兑现与反向确认','四 趋势逆转的标准流程','五 A类点3'],feature:'失效后独立反向链',detail:['先前空头预期因压力被收复而失效；只有随后独立完成30分钟A类做多结构、点3及3分钟触发才考虑多。','先前多头预期因支撑被失守而失效；只有随后独立完成30分钟A类做空结构、点3及3分钟触发才考虑空。']},
    {id:'next-unit',title:['外层点5，事前划定新单元','外层点5，事前划定新单元'],mode:'continuation',category:'结构细分',refs:['二·5 点1、点2、点3·编号必须带层级','三·3 延续交易的完整判断链'],feature:'新单元点3 · 外层点5',detail:['外层第5点不能自动开仓。上一段结束后、此次回撤发生前，已把外层3→4→5登记为新单元1→2→3，再完成许可和触发。','外层第5点不能自动开仓。上一段结束后、此次反弹发生前，已把外层3→4→5登记为新单元1→2→3，再完成许可和触发。']},
    {id:'opening',title:['开盘仍在既有点3区内转强','开盘仍在既有点3区内转弱'],mode:'continuation',category:'开盘情境',refs:['二·8 开盘或直接突破','二·9 点3内部突破与追突破的区别'],feature:'开盘 · 已有点3',detail:['盘前已存在有效主结构许可和点3计划。开盘未跳出允许区，随后完成3分钟回撤和收盘触发；开盘本身不发许可。','盘前已存在有效主结构许可和点3计划。开盘未跳出允许区，随后完成3分钟回抽和收盘触发；开盘本身不发许可。']}
  ];
  const DAY=86400000,MIN=60000,T0=Date.parse('2026-01-05T09:00:00+08:00'),PERMIT=T0+49*30*MIN,CUTOFF=PERMIT+27*MIN;
  const round=n=>Math.round(n*100)/100;
  function candles(anchors,count,step,start){const out=[];for(let i=0;i<count;i++){let k=0;while(k+1<anchors.length&&anchors[k+1][0]<i)k++;const a=anchors[k],b=anchors[Math.min(k+1,anchors.length-1)],r=b[0]===a[0]?0:(i-a[0])/(b[0]-a[0]),target=a[1]+(b[1]-a[1])*Math.max(0,Math.min(1,r)),j=anchors.some(p=>p[0]===i)?0:Math.sin(i*2.1)*.28,close=round(target+j),open=round(i?out[i-1].close:close-.4),wick=.35+(i%3)*.07;out.push({index:i,dt:new Date(start+i*step).toISOString(),closedAt:new Date(start+(i+1)*step).toISOString(),open,high:round(Math.max(open,close)+wick),low:round(Math.min(open,close)-wick),close,volume:180+(i*37)%190,completed:true});}return out;}
  function force(bar,open,high,low,close){Object.assign(bar,{open,high,low,close});}
  function line(kind,label,points,role='key',color){return{kind,label,points:points.map(([index,price])=>({index,price})),role,...(color?{color}:{})};}
  function label(index,price,text){return{index,price,text};}
  function baseShape(family){
    const continuation=family.mode==='continuation',range=family.mode==='range',b=family.mode==='reversal_b';
    let a=continuation?[[0,92],[5,99],[9,95],[15,106],[20,102],[26,119],[30,115],[33,122],[37,114],[40,116],[43,110],[46,112],[48,116.6]]:range?[[0,110],[6,122],[12,109],[18,123],[24,110],[29,116],[32,111],[35,108.8],[39,116],[43,110],[46,111.6],[48,113.2]]:[[0,132],[5,125],[9,128],[15,115],[20,120],[26,106],[29,110],[32,99],[36,106],[39,118],[43,110],[46,111.5],[48,113.2]];
    const bars=candles(a,49,30*MIN,T0);let points=continuation?[[20,102],[33,122],[43,110]]:range?[[35,108.8],[39,116],[43,110]]:[[32,99],[39,118],[43,110]];
    const key=b?122:range?108:continuation?bars[20].low:110,obstacle=b?122:range?116:continuation?122:118;
    // Trend anchors are the actual OHLC extremities of specified progress candles.
    const up=continuation,ti=up?[4,14]:range?[30,34]:[14,25];
    if(up){force(bars[4],97.4,100,95.7,99.6);force(bars[14],103.7,107.1,98.5,106.7);}else{force(bars[ti[0]],range?114:117,range?114.5:117.5,range?110.5:114.6,range?111:115);force(bars[ti[1]],range?111:109,range?111.5:109.5,range?108.8:104.6,range?109.2:105);}
    if(!continuation&&!range){if(b)force(bars[20],119.5,122,119,121.6);else{force(bars[29],109,110,108.6,109.6);force(bars[30],109.6,109.9,105.8,106.3);}}
    if(continuation)force(bars[41],116,116.3,113.5,114.2);
    if(range){force(bars[12],110,110.4,108,109);force(bars[18],122,124,121.5,123);force(bars[24],111,111.4,108,110);}
    points=points.map(([i],n)=>[i,n===1?bars[i].high:bars[i].low]);
    const lines=[line('horizontal',b?'30分关键前高 · 未破':range?'事前区间下沿':continuation?'原趋势支撑 · 仍有效':'30分关键前高 · 已破',[[0,key],[61,key]],'key'),line('segment',continuation?'回撤内部高点 · 30分已收盘突破':'30分局部进攻参考 · 已收盘突破',[[continuation?40:29,continuation?bars[40].high:bars[29].high],[49,continuation?bars[40].high:bars[29].high]],'trigger'),line('trend',up?'原上涨趋势线 · 关键K底部':'原下降趋势线 · 关键K顶部',ti.map(i=>[i,up?bars[i].low:bars[i].high]),'trend')];
    if(range)lines.push(line('horizontal','事前区间上沿',[[0,124],[61,124]],'key'));
    const annotations=[label(ti[0],up?bars[ti[0]].low:bars[ti[0]].high,up?'关键K：收盘破前高':'关键K：收盘破前低'),label(ti[1],up?bars[ti[1]].low:bars[ti[1]].high,up?'另一关键K低点锚':'另一关键K高点锚'),label(48,bars[48].close,'已完成：主周期许可')];
    return{bars,lines,points,key,obstacle,annotations,continuation,range,b};
  }
  function create(family,short){
    const side=short?1:0,direction=short?'short':'long',move=short?'转弱':'转强',breakWord=short?'跌破':'突破',retest=short?'反弹':'回撤',p=v=>round(short?220-v:v),price=v=>String(p(v)),shape=baseShape(family);
    let entry=112.4,trigger=112,stop=108.4,zoneLow=108.8,zoneHigh=114.8;
    const exec=candles([[0,115],[5,112],[9,113.2],[15,111],[20,110.6],[24,112],[28,111],[31,109.4],[33,111.6],[36,110.3],[38,111],[39,entry]],40,3*MIN,PERMIT-31*3*MIN);
    force(exec[27],111.24,111.3,109.7,110.2);force(exec[28],110.2,110.9,110,110.8);force(exec[29],110.8,111.3,110.5,111);force(exec[30],111,111.1,109.6,109.8);force(exec[31],109.8,110.3,109,109.4);force(exec[32],109.4,111.1,109.2,110.8);force(exec[33],110.8,112,110.6,111.6);force(exec[36],111,111.2,109.9,110.3);force(exec[38],110.6,111.2,110.2,111);force(exec[39],111,112.6,110.8,entry);
    const elines=[line('segment','事前局部反弹高点 · 33已收盘突破',[[29,111.3],[33,111.3]],'key')],annotations=[],extra=[],assumptions=[];let p1=31,p2=33,p3=36;
    if(family.id.startsWith('2b')){
      // Establish the reference first; only bar 31 may start this failure-break sequence.
      force(exec[18],111,111.2,family.id==='2b-zone'?109.8:110,110.8);
      force(exec[27],111.24,111.3,110.1,110.2);force(exec[30],111,111.1,110,110.2);
      assumptions.push('2B演示参数事前固定：3分钟、从首次越界当根计最多3根完成收回，越界与收回缓冲0.1；这不是系统统一默认值。');
      elines.push(line('segment',family.id==='2b-zone'?'支撑区上沿':'2B预选前低',[[18,family.id==='2b-zone'?110.8:110],[39,family.id==='2b-zone'?110.8:110]],'key'));
      if(family.id==='2b-wick'){force(exec[31],110.6,111.3,108.7,110.8);annotations.push(label(31,108.7,'实际刺穿 → 当根收回'));}
      if(family.id==='2b-multibar'){force(exec[31],110.4,110.6,108.8,109.4);force(exec[32],109.4,111.1,109.2,110.8);annotations.push(label(31,109.4,'第1根：收盘破位'),label(32,110.8,'第2根：收回完成'));}
      if(family.id==='2b-zone'){force(exec[31],110.8,111,109.1,110.2);force(exec[32],110.2,111.4,110,111.2);elines.push(line('segment','支撑区下沿',[[18,109.8],[39,109.8]],'key'));annotations.push(label(31,109.1,'跌出下沿 · 收在区内仍待定'),label(32,111.2,'收复上沿才完成'));}
      extra.push('收回仅完成形态；后续仍有独立局部进攻、点3验证和更晚的局部收盘触发。');
    }
    if(family.id==='inside-child'||family.id==='inside-outer'){
      force(exec[34],112.7,113.4,109.6,110.4);force(exec[35],110.8,112,110.2,111.4);force(exec[36],111.4,111.8,110.4,110.9);force(exec[37],110.9,111.7,110.6,111.3);force(exec[38],111.3,111.8,110.8,111.5);
      stop=109.2;trigger=family.id==='inside-child'?112:113.4;entry=round(trigger+.4);force(exec[39],111.5,entry+.2,111.2,entry);p1=31;p2=34;p3=36;
      elines.push(line('segment','母线高点',[[34,113.4],[40,113.4]],'key'),line('segment','子线高点',[[35,112],[40,112]],'key'),line('segment','组合低点 · 本例防守',[[34,109.6],[40,109.6]],'stop'));
      annotations.push(label(34,113.4,'母线已收盘'),label(35,110.2,'严格内包子线已收盘'));
      assumptions.push('包含组合先完成，再等待最多4根3分钟K；触发收盘越界0.1以上，本例按组合低点外0.4设置保护。');
      extra.push(family.id==='inside-child'?'本例事前选子线触发，成交仍低于母线高点；不要求改追组合突破。':'本例事前选组合外沿触发，子线突破时仍等待；最后成交仍在点3区。');
    }
    if(family.id==='harami'){
      force(exec[34],114,114.4,109.6,110.4);force(exec[35],111.1,115,110.8,113);force(exec[36],113,113.3,111.2,111.8);force(exec[37],111.8,113.3,111.4,112.9);force(exec[38],112.9,114.8,112.6,114.2);
      trigger=115;entry=115.4;stop=109.2;zoneHigh=116;force(exec[39],114.2,115.6,114,entry);p2=35;p3=36;
      elines.push(line('segment','母线最高 · 不是组合最高',[[34,114.4],[40,114.4]],'key'),line('segment','子线影线 · 实际组合上沿',[[35,115],[40,115]],'trigger'),line('segment','实际组合下沿',[[34,109.6],[40,109.6]],'stop'));
      annotations.push(label(34,110.4,'母线实体范围'),label(35,115,'子实体在内，影线越界'));
      assumptions.push('本例事前选实际组合外沿触发；母子线实际最低价外0.4止损，不能为了少亏改成子线内的任意价位。');
      extra.push('这是实体孕线，不是严格内包；组合边界含影线。仅突破母线高点尚不满足本例预选触发。');
    }
    if(family.id==='defense'){
      shape.lines.push(line('segment','与推进低点对应的防守高点',[[40,shape.bars[40].high],[48,shape.bars[40].high]],'key'));
      shape.annotations.push(label(40,shape.bars[40].high,'事前记录：对应防守点'),label(43,110,'由这段结构推进到低点'));
      extra.push('参考点在破位前已确认；不是任取前一根K线极值。该对应关系仅属于30分钟，不能自动升级为日线反转。');
    }
    if(family.id==='failed-expectation'){
      shape.lines.push(line('segment','旧空头预期：应压住的水平',[[29,110],[39,110]],'key'));
      shape.annotations.push(label(36,106,'旧预期尚未兑现'),label(39,118,'收复旧压制 · 原空头依据失效'));
      extra.push('旧单退出与新单开仓分开。原预期失效时不反手；本例在更晚的独立反向点3触发才开仓。');
    }
    if(family.id==='next-unit'){
      shape.annotations.push(label(20,102,'外层3 = 新单元1'),label(33,122,'外层4 = 新单元2'),label(43,110,'外层5 = 新单元3'));
      extra.push('新单元必须在本次点3形成与触发前登记；图中1、2、3是该新单元。外层7若另建单元，也须重新完成同一全链，不能仅按奇数开仓。');
    }
    if(family.id==='opening'){
      annotations.push(label(31,109.4,'开盘：仍在事前允许区'));
      assumptions.push('开盘情境的时间轴仅示意先后，未套用任何交易所具体开盘时刻；盘前主结构许可仍有效，开盘第1根3分钟已收盘。');
      extra.push('若实际开盘跳出允许区，或直到触发时才离区，本案例立刻不适用；直接高开/低开不替代回撤验证。');
    }
    const current=exec.at(-1);shape.bars.push({index:49,dt:new Date(PERMIT).toISOString(),closedAt:new Date(PERMIT+30*MIN).toISOString(),open:shape.bars[48].close,high:Math.max(shape.bars[48].close,...exec.slice(31).map(b=>b.high)),low:Math.min(shape.bars[48].close,...exec.slice(31).map(b=>b.low)),close:entry,volume:1300,completed:false});
    const mainNodes=shape.points.map(([index,value],i)=>({index,price:value,label:String(i+1),future:false}));mainNodes.push({index:60,price:shape.obstacle,label:'4',future:true});
    shape.lines.push(line('segment','最近障碍 · 先看这里',[[36,shape.obstacle],[61,shape.obstacle]],'obstacle'),line('segment','入场 · 3分触发后仍在区内',[[48,entry],[59,entry]],'entry'),line('segment','保护止损',[[44,stop],[59,stop]],'stop'));
    elines.push(line('segment','预选局部触发位',[[p2,trigger],[43,trigger]],'trigger'),line('segment','入场',[[39,entry],[48,entry]],'entry'),line('segment','保护止损',[[p3,stop],[48,stop]],'stop'),line('trend','局部回撤参考连线 · 不代替趋势许可',[[31,exec[31].low],[36,exec[36].low]],'reference'));
    annotations.push(label(31,exec[31].low,'主许可之后 · 局部进攻起点'),label(39,entry,'已收盘触发 · 可按案例计划入场'));
    const daily=shape.range?candles([[0,111],[6,125],[12,108],[18,124],[24,110],[31,120],[35,114]],36,DAY,T0-37*DAY):candles([[0,75],[5,83],[9,79],[15,94],[20,88],[26,109],[31,102],[35,114]],36,DAY,T0-37*DAY);
    const charts={day:{title:'日线 · 已完成背景',caption:shape.range?'已确认区间，计划允许边缘向内部交易。':'已完成日线允许本例方向与模式；本图不使用当日日线最终形态。',bars:daily,lines:[line('horizontal',shape.range?'日线区间边缘':'日线方向防守参考',[[0,shape.range?108:96],[42,shape.range?108:96]],'key'),line('horizontal','日线最近外层障碍',[[0,128],[42,128]],'obstacle'),line('trend','日线结构参考连线',[[8,daily[8].low],[19,daily[19].low]],'reference')],nodes:[],zones:[],annotations:[]},m30:{title:'30分钟 · 主结构与位置',caption:'1、2、3为30分钟交易单元；末根形成中仅显示现价。4为条件预期，后续结果未发生。',bars:shape.bars,lines:shape.lines,nodes:mainNodes,zones:[{start:40,end:60,low:zoneLow,high:zoneHigh,label:'点3允许入场区'}],annotations:shape.annotations},m3:{title:'3分钟 · 局部触发放大',caption:'主周期许可之后，再完成局部验证及更晚的收盘触发；局部编号不升级主周期等级；局部4是条件延伸，不是30分钟目标。',bars:exec,lines:elines,nodes:[{index:p1,price:exec[p1].low,label:'1'},{index:p2,price:exec[p2].high,label:'2'},{index:p3,price:exec[p3].low,label:'3'},{index:48,price:Math.min(shape.obstacle,entry+1.2),label:'4',future:true}],zones:[{start:29,end:48,low:zoneLow,high:zoneHigh,label:'同一主结构点3区'}],annotations}};
    const swaps=[['低于','高于'],['应压住','应守住'],['旧压制','旧支撑'],['已收盘突破','已收盘跌破'],['收盘破位','收盘上破'],['母线高点','母线低点'],['子线高点','子线低点'],['原上涨趋势线','原下跌趋势线'],['原下降趋势线','原上升趋势线'],['关键前高','关键前低'],['前高','前低'],['高点','低点'],['最高','最低'],['上沿','下沿'],['上方','下方'],['上涨','下跌'],['上攻','下压'],['底部','顶部'],['低点锚','高点锚'],['做多','做空'],['多头','空头'],['支撑','压力'],['转强','转弱'],['向上','向下'],['回撤内部','反弹内部'],['收复','跌回'],['跌出','越过'],['上破','下破']];
    const escaped=t=>t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const dictionary=new Map(swaps.flatMap(([a,b])=>[[a,b],[b,a]])),pattern=new RegExp([...dictionary.keys()].sort((a,b)=>b.length-a.length).map(escaped).join('|'),'g');
    const mirrorText=text=>short?String(text).replace(pattern,value=>dictionary.get(value)):text;
    for(const chart of Object.values(charts)){
      chart.bars=chart.bars.map(bar=>({...bar,open:p(bar.open),close:p(bar.close),high:p(short?bar.low:bar.high),low:p(short?bar.high:bar.low)}));
      chart.lines=chart.lines.map(l=>({...l,label:mirrorText(l.label),points:l.points.map(point=>({...point,price:p(point.price)})),color:l.role==='entry'?(short?'#22c55e':'#ef5350'):l.role==='stop'?'#d7ac66':l.role==='trend'?'#d3bc45':l.role==='trigger'?'#6fb6d6':'#e3c948'}));
      chart.nodes=chart.nodes.map(n=>({...n,price:p(n.price)}));chart.zones=chart.zones.map(z=>({...z,low:p(short?z.high:z.low),high:p(short?z.low:z.high)}));chart.annotations=chart.annotations.map(a=>({...a,price:p(a.price),text:mirrorText(a.text)}));
    }
    const modeNames={continuation:'趋势延续',reversal_a:'结构反转 A',reversal_b:'结构反转 B',range:'区间边缘'},loss=round(Math.abs(entry-stop)*50+20),interval=[p(short?zoneHigh:zoneLow),p(short?zoneLow:zoneHigh)],directionName=short?'做空':'做多';
    return{id:family.id+'-'+direction,title:family.title[side],family:family.id,category:family.category,mode:family.mode,modeLabel:modeNames[family.mode],direction,tags:[family.feature,directionName],summary:family.detail[side],stage:`${modeNames[family.mode]} · 主结构点3验证后 · 3分钟已收盘${move}`,context:shape.range?'日线区间背景，事前允许区间边缘方向。':`已完成日线结构与盘前计划允许${directionName}及${modeNames[family.mode]}，不把盘中日K当收盘确认。`,prerequisites:[`日线方向和主模式已事先允许${directionName}；未使用未来K线。`,shape.continuation?`原方向仍有效；30分钟已收盘${breakWord}预选回撤内部结构，获得执行许可。`:shape.b?`30分钟原趋势线已破、反向局部进攻成立，关键水平${price(shape.key)}仍未突破；按B类。`:shape.range?'事前区间和上下边缘已确认；本次位于边缘，有独立局部进攻与回撤。':`30分钟趋势线与关键水平${price(shape.key)}已突破，点3验证未失效；按A类。`,`主许可之后完成3分钟局部${retest}验证；更晚的3分钟收盘${breakWord}${price(trigger)}。`,`触发价与假定成交价${price(entry)}都在${interval.join('—')}允许区内；防守和最近障碍已标明。`,...extra.map(mirrorText)],entry:{price:p(entry),stop:p(stop),zoneLow:interval[0],zoneHigh:interval[1],obstacle:p(shape.obstacle),trigger:p(trigger),cover:`掩体：${family.id.startsWith('inside')||family.id==='harami'?'已完成母子线实际组合极值':family.id.startsWith('2b')?'事前选定的2B刺穿极值':'点3已完成的局部防守结构'}外侧，保护价${price(stop)}。保护止损触发即执行，不等30分钟收盘。`,protection:`平推/保本不是系统统一固定价。本例未预设机械平推；先观察${price(shape.obstacle)}附近的进攻能否保持。若事前启用获利保护，应另写触发、费用后的保护价和执行标准，不凭首次触碰障碍自动平推。`,risk:{capital:50000,budget:1000,lots:1,multiplier:50,fee:12,slippage:8,loss,twoLotsLoss:round(loss*2),assumption:'教学假定：每点每手50元，往返费用12元/手，滑点预算8元/手；非任何实际合约参数。单笔预算1000元按用户设置。'}},steps:[{key:'point1',label:'① 先定结构',text:`事前选定日线环境、${modeNames[family.mode]}、关键水平与允许区。点1是本交易单元起点。`},{key:'point2',label:'② 进攻确认，不追',text:shape.b?'局部进攻和趋势线破坏已完成，主关键水平未破，仍按B类；点2不开仓。':shape.continuation?'主周期回撤结束必须由预定内部结构的收盘突破确认，不要求追破趋势最远端。':'主周期相应进攻已完成；点2用于确认，不在突破时追价。'},{key:'point3',label:'③ 验证到位，继续等',text:`回撤/反弹进入预选${interval.join('—')}，既定防守未失效；触碰区域本身不是信号。`},{key:'trigger',label:'④ 点3内触发，才入场',text:`主许可后的局部验证已完成，更晚的3分钟收盘${breakWord}${price(trigger)}；案例按${price(entry)}成交、${price(stop)}止损，1手计划损失${loss}元。图中浪线4仅表示有条件延伸。`}],invalidations:[`价格触发保护止损${price(stop)}，立即执行保护，不等待更大周期收盘。`,`若30分钟按预定标准${short?'收复':'跌破'}允许区${short?'上沿':'下沿'}，取消原点3依据；退出不自动反手。`,shape.b?`B类到${price(shape.obstacle)}仍未突破：本例事前选“受阻后3分钟收盘反向破推进防守点”退出；首次触碰或短暂停顿不自动判失败。`:`最近障碍${price(shape.obstacle)}附近，按事前推进失败条件处理，不默认一定突破或走完整个区间。`],avoid:['只有点2突破，没有点3验证，不开。','只有3分钟信号、30分钟尚未许可，不开。',`触发/预期成交离开${interval.join('—')}，不追。`,'触发保护后临时扩大止损、改周期或重编号，不做。',family.id.startsWith('2b')?'只凭2B收回立即反手，属于未自动纳入的独立早试。':family.id.startsWith('inside')||family.id==='harami'?'只凭包含形态，或在子线未收盘时抢入，不做。':'原方向偏好不替代完成条件；没有验证就接受错过。'],assumptions:['所有K线、价格、成交与风险参数均为教学构造，不对应当前合约或真实成交；不提供胜率。日线、30分钟、3分钟分别展示所属层级的结构，不是同一真实合约逐级聚合的行情回放。','本例主周期收盘越界缓冲0.1、执行触发缓冲0.1，局部触发最多等待4根3分钟；参数在本例发生前固定，不是系统新加的统一阈值。','时间采用连续教学标尺，仅表达日线背景先完成、30分钟许可在先、3分钟验证及触发在后，不表示任何品种的交易日历。',...assumptions.map(mirrorText)],ruleRefs:family.refs,outcome:'仅画到已完成的入场触发；虚线4是条件预期，未展示后续盈亏。',evidence:{dailyClosedAt:daily.at(-1).closedAt,mainPermissionAt:new Date(PERMIT).toISOString(),localValidationAt:exec[p3].closedAt,triggerAt:current.closedAt,cutoff:new Date(CUTOFF).toISOString()},charts};
  }
  const cases=families.flatMap(family=>[create(family,false),create(family,true)]);
  return{version:1,source,cases,coverage:{families:14,examples:28,coreModes:4,scope:'覆盖现行规则的主要许可分支与多空镜像；附加形态是同一主模式的条件示例，不穷举市场走势组合。'},get:id=>cases.find(c=>c.id===id)};
}));
