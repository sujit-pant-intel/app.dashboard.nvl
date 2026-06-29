"""_tab_charts.py — Charts (distribution) tab: HTML panel + JavaScript functions."""
from _tab_registry import Tab

TAB_ID     = 'tab-dist'
TAB_LABEL  = 'Charts'
TAB_ACTIVE = False


def tab_html() -> str:
    return '''
<div id="tab-dist" class="tab-panel">
  <div class="col-pills" id="col-pills"></div>
  <div style="display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap">
    <div class="xy-resize-wrap" style="flex:0 0 36%;min-width:300px;position:relative">
      <h3 id="scatter-title" style="margin:0 0 4px;font-size:12px;color:#2c3e50">UPM vs Selected Column</h3>
      <div style="font-size:11px;color:#888;margin-bottom:4px">
        <button class="scatter-ylog-btn" onclick="_toggleScatterYLog()" style="font-size:11px;padding:2px 8px;cursor:pointer;border:1px solid #7f8c8d;border-radius:4px;background:#2c3e50;color:#fff" title="Toggle Y-axis between linear and log scale">Y: Log</button>
      </div>
      <svg id="scatter-svg" style="width:100%;aspect-ratio:1/1;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>
      <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>
      <div class="chart-note" id="scatter-note" style="font-size:16px;color:#2c3e50;margin-top:4px"></div>
    </div>
    <div class="xy-resize-wrap" style="flex:0 0 48%;min-width:300px;position:relative">
      <h3 style="margin:0 0 4px;font-size:18px;color:#2c3e50">Pareto (per wafer)</h3>
      <div style="font-size:14px;color:#888;margin-bottom:4px;line-height:1.6">X-axis: <label style="margin-left:4px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="none" onchange="_toggleParetoGroup('none')"> None</label><label style="margin-left:6px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="program" onchange="_toggleParetoGroup('program')"> Program</label><label style="margin-left:6px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="lot" onchange="_toggleParetoGroup('lot')" checked> Lot</label><label style="margin-left:6px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="wafer" onchange="_toggleParetoGroup('wafer')" checked> Wafer</label><label style="margin-left:6px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="material" onchange="_toggleParetoGroup('material')"> Material</label></div>
      <svg id="pareto-svg" style="width:100%;aspect-ratio:2/1.125;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>
      <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>
      <div class="chart-note" id="pareto-note" style="font-size:12px;color:#7f8c8d;margin-top:4px;text-align:left"></div>
    </div>
  </div>
  <div style="margin-top:48px">
    <div class="xy-resize-wrap" style="max-width:95%;position:relative">
      <h3 style="margin:0 0 4px;font-size:12px;color:#2c3e50">Distribution</h3>
      <svg id="hist-svg" style="width:100%;aspect-ratio:1/0.45;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>
      <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>
      <div class="chart-note" id="chart-note" style="font-size:15px;color:#7f8c8d;margin-top:4px"></div>
      <div id="dist-stats-tbl" style="margin-top:6px"></div>
    </div>
    <div class="xy-resize-wrap" style="max-width:95%;margin-top:36px;position:relative">
      <div id="dist-mini-upm-panel">
        <h3 id="dist-mini-upm-title" style="margin:0 0 4px;font-size:12px;color:#c0650a">UPM Distribution</h3>
        <svg id="dist-mini-upm-svg" style="width:100%;aspect-ratio:1/0.45;display:block;border:1px solid #f5e0c3;border-radius:4px;background:#fffaf4"></svg>
        <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>
        <div id="dist-mini-upm-note" style="font-size:9px;color:#c0650a;margin-top:2px"></div>
      </div>
    </div>
  </div>
</div>
'''


def tab_js() -> str:
    return '''
var FT = {};
var _ddOpen = null;
function buildPills(){
  var div=document.getElementById('col-pills');
  if(!div)return;
  var html='';var prevS=null;
  ALL_COLS.forEach(function(c){
    var s=UPM_COLS.indexOf(c)>=0?'upm':'sicc';
    if(s!==prevS&&prevS!==null)html+='<hr class="pill-sep">';
    prevS=s;
    var act=(c===SEL_COL&&!IS_CDYN)?' active':'';
    html+='<button class="pill'+act+'" data-c="'+esc(c)+'" onclick="pClick(this,false)">'+esc(c)+'</button>';
  });
  if(CDYN_COLS.length){
    html+='<hr class="pill-sep">';
    CDYN_COLS.forEach(function(c){
      var act=(c===SEL_COL&&IS_CDYN)?' active':'';
      html+='<button class="pill cdyn-pill'+act+'" data-c="'+esc(c)+'" onclick="pClick(this,true)">'+esc(c)+'</button>';
    });
  }
  div.innerHTML=html;
}
function pClick(btn,cdyn){
  var col=btn.dataset.c;
  SEL_COL=col;IS_CDYN=!!cdyn;
  var active=document.querySelector('.tab-panel.active');
  if(active&&active.id==='tab-dist'){renderHist();}
  else if(cdyn){render_cdyn();}
  else{render_sicc();}
}
window.pClick=pClick;
function renderHist(){
  buildPills();
  var _distCfg={isCdyn:IS_CDYN,histSvg:'hist-svg',statsTbl:'dist-stats-tbl',noteEl:'chart-note',distTitle:null,scatterSvg:'scatter-svg',scatterTitle:'scatter-title',scatterNote:'scatter-note',miniSvg:'dist-mini-upm-svg',miniTitle:'dist-mini-upm-title',miniNote:'dist-mini-upm-note'};
  if(!SEL_COL){_renderDistBody([],null,_distCfg);drawPareto([],null);return;}
  var ai=getFiltered();
  var active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  var tgt=IS_CDYN?CDYN_TARGETS[SEL_COL]:TARGETS[SEL_COL.toUpperCase()];
  _renderDistBody(active,SEL_COL,_distCfg);
  drawPareto(active,tgt);
}
function drawPareto(active,tgt){
  var svg=document.getElementById('pareto-svg');
  var note=document.getElementById('pareto-note');
  if(!svg)return;
  if(!SEL_COL||!active||!active.length){svg.innerHTML='';if(note)note.textContent='';return;}
  var _groups={};
  active.forEach(function(i){
    var r=ROWS[i];
    var v=IS_CDYN?r.cdyn[SEL_COL]:r.medians[SEL_COL];
    if(v!=null&&!isNaN(v)&&v>0){
      var parts=[];
      if(PARETO_GROUP.indexOf('program')>=0)parts.push(r.program||'?');
      if(PARETO_GROUP.indexOf('lot')>=0)parts.push(r.lot);
      if(PARETO_GROUP.indexOf('wafer')>=0)parts.push(r.wafer);
      if(PARETO_GROUP.indexOf('material')>=0)parts.push(r.material||'?');
      var lbl=parts.length?parts.join('/'):r.lot+'/'+r.wafer;
      if(!_groups[lbl])_groups[lbl]={vals:[],indices:[]};
      _groups[lbl].vals.push(v);
      _groups[lbl].indices.push(i);
    }
  });
  var pts=[];
  Object.keys(_groups).forEach(function(lbl){
    var g=_groups[lbl];
    var med=medArr(g.vals);
    if(med!=null)pts.push({label:lbl,val:med,idx:g.indices[0],count:g.vals.length});
  });
  if(!pts.length){svg.innerHTML='';if(note)note.textContent='No data.';return;}
  pts.sort(function(a,b){return b.val-a.val;});
  var W=Math.max(svg.clientWidth||500,260),H=svg.clientHeight||383;
  var pl=78,pr=98,pt=32,pb=200;
  var cW=W-pl-pr,cH=H-pt-pb;
  var n=pts.length;
  var maxV=pts[0].val||1;
  if(tgt!=null&&tgt>maxV)maxV=tgt*1.05;
  var bw=Math.min(cW/n,80);
  var barArea=bw*n;
  var xOff=pl+(cW-barArea)/2;
  var p=['<rect width="'+W+'" height="'+H+'" fill="#f8f9fa"/>'];
  p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+(pt-20)+'" text-anchor="middle" font-size="15" fill="#333" font-weight="bold">'+esc(SEL_COL)+'</text>');
  for(var i=0;i<n;i++){
    var bh=(pts[i].val/maxV)*cH;
    var bx=xOff+i*bw;
    var by=pt+cH-bh;
    var col=(tgt!=null&&pts[i].val>tgt)?'#e74c3c':'#3498db';
    p.push('<rect x="'+(bx+1).toFixed(1)+'" y="'+by.toFixed(1)+'" width="'+Math.max(1,bw-2).toFixed(1)+'" height="'+Math.max(1,bh).toFixed(1)+'" fill="'+col+'" opacity="0.82"/>');
    var _tx=(bx+bw/2).toFixed(1);
    if(bh>40){p.push('<text x="'+_tx+'" y="'+(by+14).toFixed(1)+'" text-anchor="middle" font-size="12" fill="#fff" transform="rotate(-90,'+_tx+','+(by+14).toFixed(1)+')">'+ pts[i].val.toFixed(4)+'</text>');}
    else{p.push('<text x="'+_tx+'" y="'+(by-3).toFixed(1)+'" text-anchor="middle" font-size="12" fill="#333" transform="rotate(-90,'+_tx+','+(by-3).toFixed(1)+')">'+ pts[i].val.toFixed(4)+'</text>');}
  }
  if(tgt!=null){
    var ty=pt+cH-(tgt/maxV)*cH;
    if(ty>=pt-2&&ty<=pt+cH+2){
      p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+ty.toFixed(1)+'" y2="'+ty.toFixed(1)+'" stroke="#27ae60" stroke-width="2"/>');  
      p.push('<text x="'+(pl+cW*0.75).toFixed(1)+'" y="'+(ty-4).toFixed(1)+'" font-size="14" fill="#27ae60" font-weight="bold">Exp:'+Number(tgt).toFixed(4)+'</text>');
      p.push('<line x1="'+(pl-6)+'" x2="'+pl+'" y1="'+ty.toFixed(1)+'" y2="'+ty.toFixed(1)+'" stroke="#27ae60" stroke-width="2"/>');
      p.push('<text x="'+(pl-8)+'" y="'+(ty+4).toFixed(1)+'" text-anchor="end" font-size="13" fill="#27ae60" font-weight="bold">Exp:'+Number(tgt).toFixed(4)+'</text>');
    }
  }
  var ySteps=5;var yStep=maxV/ySteps;
  for(var yi=0;yi<=ySteps;yi++){
    var yt=yi*yStep;
    var yy=pt+cH-(yt/maxV)*cH;
    p.push('<line x1="'+(pl-4)+'" x2="'+pl+'" y1="'+yy.toFixed(1)+'" y2="'+yy.toFixed(1)+'" stroke="#aaa"/>');
    p.push('<text x="'+(pl-6)+'" y="'+(yy+4).toFixed(1)+'" text-anchor="end" font-size="17" fill="#444">'+yt.toFixed(4)+'</text>');
  }

  for(var i=0;i<n;i++){
    var tx=xOff+i*bw+bw/2;
    p.push('<text x="'+tx.toFixed(1)+'" y="'+(pt+cH+8)+'" text-anchor="end" transform="rotate(-45,'+tx.toFixed(1)+','+(pt+cH+8)+')" font-size="11" fill="#333">'+esc(pts[i].label)+'</text>');
  }
  p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+(pt+cH)+'" y2="'+(pt+cH)+'" stroke="#aaa"/>');
  p.push('<line x1="'+pl+'" x2="'+pl+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#aaa"/>');
  // UPM overlay on Pareto: per-wafer UPM median as orange diamond markers
  var uCol=_getUpmCol(SEL_COL);
  if(uCol){
    var uPts=[],uMax=-Infinity,uMin=Infinity;
    for(var i=0;i<n;i++){
      var r=ROWS[pts[i].idx];
      var uv=r.medians[uCol];
      if(uv!=null&&!isNaN(uv)){uPts.push({i:i,v:uv});if(uv>uMax)uMax=uv;if(uv<uMin)uMin=uv;}
    }
    if(uPts.length>1&&uMax>uMin){
      var uRange=uMax-uMin;if(uRange===0)uRange=1;
      // Right Y-axis for UPM %
      var uYSteps=4;
      for(var yi=0;yi<=uYSteps;yi++){
        var uv2=uMin+yi*(uRange/uYSteps);
        var uy2=pt+cH-((uv2-uMin)/uRange)*cH;
        p.push('<text x="'+(pl+cW+18)+'" y="'+(uy2+3).toFixed(1)+'" text-anchor="start" font-size="17" fill="#d35400">'+uv2.toFixed(1)+'</text>');
      }

      p.push('<text x="'+(pl+cW+80)+'" y="'+(pt+cH/2).toFixed(1)+'" text-anchor="middle" font-size="17" fill="#d35400" font-weight="bold" transform="rotate(-90,'+(pl+cW+80)+','+(pt+cH/2)+')">UPM%</text>');
      // Draw UPM line + markers
      var uLine='';
      uPts.forEach(function(up){
        var cx=xOff+up.i*bw+bw/2;
        var cy=pt+cH-((up.v-uMin)/uRange)*cH;
        uLine+=cx.toFixed(1)+','+cy.toFixed(1)+' ';
        p.push('<polygon points="'+cx+','+(cy-4)+' '+(cx+4)+','+cy+' '+cx+','+(cy+4)+' '+(cx-4)+','+cy+'" fill="#d35400" opacity="0.85"/>');
      });
    }
  }
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  svg.innerHTML=p.join('');
  if(note)note.textContent=n+' group(s), sorted descending (median per group).'+(uCol?' Diamonds = UPM %':'');
}
function ftOpen(field,btn){
  if(_ddOpen)_ddClose();
  var vals=getFieldVals(field);
  var panel=document.createElement('div');panel.className='dd-panel';
  panel.innerHTML='<input class="dds" placeholder="Search…">'
    +'<div class="dda"><button>All</button><button>Clear</button></div>'
    +'<div class="ddl" id="_ddl"></div>'
    +'<div class="ddf"><button>OK</button></div>';
  document.body.appendChild(panel);
  var r=btn.getBoundingClientRect();
  panel.style.top=(r.bottom+2+window.scrollY)+'px';
  panel.style.left=Math.min(r.left,window.innerWidth-220)+'px';
  _ddOpen={panel:panel,field:field,btn:btn,vals:vals,chk:FT[field]?new Set(FT[field]):new Set(vals)};
  _ddRender(vals);
  panel.querySelector('.dds').oninput=function(){
    var q=this.value.toLowerCase();
    _ddRender(q?vals.filter(function(v){return String(v).toLowerCase().indexOf(q)>=0;}):vals);
  };
  var acts=panel.querySelectorAll('.dda button');
  acts[0].onclick=function(){_ddOpen.vals.forEach(function(v){_ddOpen.chk.add(v);});_ddRender(_ddOpen.vals);};
  acts[1].onclick=function(){_ddOpen.chk.clear();_ddRender(_ddOpen.vals);};
  panel.querySelector('.ddf button').onclick=_ddApply;
  setTimeout(function(){document.addEventListener('mousedown',_ddOut);},0);
}
window.ftOpen=ftOpen;
function _ddRender(vals){
  var list=document.getElementById('_ddl');if(!list)return;
  list.innerHTML=vals.map(function(v){
    return '<label class="ddi"><input type="checkbox"'+ (_ddOpen.chk.has(v)?' checked':'')+' data-val="'+esc(String(v))+'">'+esc(String(v))+'</label>';
  }).join('');
  list.querySelectorAll('input').forEach(function(inp){
    inp.onchange=function(){
      if(inp.checked)_ddOpen.chk.add(inp.dataset.val);
      else _ddOpen.chk.delete(inp.dataset.val);
    };
  });
}
function _ddApply(){
  if(!_ddOpen)return;
  var field=_ddOpen.field,chk=_ddOpen.chk,vals=_ddOpen.vals;
  FT[field]=(chk.size===vals.length)?null:new Set(chk);
  var btn=document.getElementById('ft-'+field);
  if(btn){
    btn.classList.toggle('active',FT[field]!=null);
    btn.textContent=(FT[field]?field+' ('+FT[field].size+'/'+vals.length+')':' All')+' ▼';
  }
  _ddClose();
  SEL_WFR.clear();
  updateAll();
}
function _ddClose(){
  document.removeEventListener('mousedown',_ddOut);
  if(_ddOpen&&_ddOpen.panel.parentNode)_ddOpen.panel.parentNode.removeChild(_ddOpen.panel);
  _ddOpen=null;
}
function _ddOut(e){if(_ddOpen&&!_ddOpen.panel.contains(e.target))_ddApply();}
registerTab('tab-dist', renderHist, true);
'''


build_tab = Tab(
    tab_id=TAB_ID,
    label=TAB_LABEL,
    active=TAB_ACTIVE,
    html_fn=tab_html,
    js_fn=tab_js,
)
