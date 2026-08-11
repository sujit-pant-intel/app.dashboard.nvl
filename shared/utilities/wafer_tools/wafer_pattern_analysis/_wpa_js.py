"""
_wpa_js.py — single-source JavaScript for WPA pattern scoring.

The functions here are the canonical implementation.
scorer.py is a Python mirror of _wmScorePattern / _wmScoreReticle.
Keep thresholds in sync between the two files.

Last verified in sync with _pipeline_html.py: 2026-05-16
"""

# ── CSS ───────────────────────────────────────────────────────────────────────

WPA_SCORE_JS = r"""
function _wmIbColor(ib){
  if(ib===null||ib===undefined)return"#e0e0e0";
  var c=WM_PAT.ibColors&&WM_PAT.ibColors[String(parseInt(ib))];
  return c||"#aaaaaa";
}
var _wmFailThr=3;var _wmEdgeExcRows=1;
function _wmIsFail(ib){if(ib===null||ib===undefined)return false;var n=parseInt(ib);return n>=_wmFailThr;}
function _wmSetFailThr(v){
  _wmFailThr=+v;
  document.querySelectorAll("input[name='wm-thr-rb']").forEach(function(rb){rb.checked=(+rb.value===_wmFailThr);});
  if(typeof _wmPatRender==="function")_wmPatRender();
}
function _wmSetEdgeRows(n){
  _wmEdgeExcRows=+n;
  document.querySelectorAll("select.wm-edge-sel").forEach(function(s){s.value=String(_wmEdgeExcRows);});
  if(typeof _wmPatRender==="function")_wmPatRender();
}

/* _wmScorePattern — spatial pattern scoring.
   Inputs: normalized coords (x-ctr)/xRad, (y-ctr)/yRad so wafer fits unit disk.
   Returns {center, edge, donut, systematic, random: 0-1, confidence: LOW/MEDIUM/HIGH} */
function _wmScorePattern(failXn,failYn){
  var N=failXn.length;
  if(!N)return{center:0,edge:0,donut:0,systematic:0,random:1,confidence:"LOW"};
  var B1=0,B2=0,B3=0,B4=0,B5=0,B6=0,q=[0,0,0,0];
  for(var i=0;i<N;i++){
    var r=Math.sqrt(failXn[i]*failXn[i]+failYn[i]*failYn[i]);
    if(r<0.15)B1++;else if(r<0.40)B2++;else if(r<0.60)B3++;else if(r<0.75)B4++;else if(r<0.90)B5++;else B6++;
    var xi=failXn[i],yi=failYn[i];
    if(xi>=0&&yi>=0)q[0]++;else if(xi<0&&yi>=0)q[1]++;else if(xi<0&&yi<0)q[2]++;else q[3]++;
  }
  var fC=(B1+B2)/N,eC=0.16;
  var fE=(B5+B6)/N,eE=0.4375;
  var fM=(B3+B4)/N,eM=0.4025;
  var centerScore=Math.max(0,Math.min(1,(fC-eC)/(1-eC)));
  var edgeScore  =Math.max(0,Math.min(1,(fE-eE)/(1-eE)));
  var midEnrich  =Math.max(0,(fM-eM)/(1-eM));
  var donutScore =Math.min(1,midEnrich*2*(1-Math.max(centerScore,edgeScore)*0.7));
  var sampleConf =Math.min(1,N/20);
  var qImbal=(Math.max.apply(null,q)-Math.min.apply(null,q))/N;
  var systematicScore=Math.min(1,qImbal*2.5)*sampleConf;
  var dominated=Math.max(centerScore,edgeScore,donutScore,systematicScore);
  var randomScore=Math.max(0,Math.min(1,1-dominated));
  var conf=N<20?"LOW":N<50?"MEDIUM":"HIGH";
  return{center:+centerScore.toFixed(2),edge:+edgeScore.toFixed(2),donut:+donutScore.toFixed(2),
         systematic:+systematicScore.toFixed(2),random:+randomScore.toFixed(2),confidence:conf};
}

/* _wmScoreReticle — reticle-correlated pattern score.
   retMap: {"sort_x,sort_y": [site_x, site_y, shot_idx]}
   siteTotals: {"site_x,site_y": total_shots} */
function _wmScoreReticle(actX,actY,rm,st){
  if(!rm)rm=WM_PAT.retMap;if(!st)st=WM_PAT.retSiteTotals;
  if(!rm||!st||!actX||!actX.length)return 0;
  var siteShots={},siteCnt={},N=actX.length;
  for(var i=0;i<N;i++){
    var info=rm[actX[i]+","+actY[i]];if(!info)continue;
    var sk=info[0]+","+info[1];var si=String(info[2]);
    if(!siteShots[sk]){siteShots[sk]={};siteCnt[sk]=0;}
    siteShots[sk][si]=true;siteCnt[sk]++;
  }
  var sites=Object.keys(siteShots);if(!sites.length)return 0;
  var maxSiteScore=0,weightedSum=0,totalMapped=0;
  sites.forEach(function(sk){
    var totShots=st[sk]||1;
    var failShots=Object.keys(siteShots[sk]).length;
    var score=failShots/totShots;
    var cnt=siteCnt[sk];
    totalMapped+=cnt;weightedSum+=score*cnt;
    if(score>maxSiteScore)maxSiteScore=score;
  });
  if(!totalMapped)return 0;
  var raw=(weightedSum/totalMapped)*0.4+maxSiteScore*0.6;
  var sampleConf=Math.min(1,N/15);
  return Math.min(1,raw*sampleConf);
}

function _wmPrimary(sc){
  var best="random",bv=sc.random||0;
  ["center","edge","donut","systematic","reticle"].forEach(function(k){
    if(sc[k]!==undefined&&sc[k]>bv){bv=sc[k];best=k;}
  });
  return{center:"CENTER",edge:"EDGE",donut:"DONUT",systematic:"SYSTEMATIC",
         reticle:"RETICLE",random:"RANDOM"}[best]||best.toUpperCase();
}

var _pColors={CENTER:"#c0392b",EDGE:"#e67e22",DONUT:"#8e44ad",
              SYSTEMATIC:"#2471a3",RETICLE:"#1f618d",RANDOM:"#27ae60"};
"""

# ── Lot / wafer picker helpers (needed by standalone WPA) ─────────────────────
WPA_PICKER_JS = r"""
var _wmPatBinChecked=null;
var _wmPatSelWafers=null;
var _wmPatCurLots=null;
var _wmPatRetUnchecked=null;
var _wmPatShotUnchecked=null;
var _wmShotAllSis=[];

function _wmPatGetLot(k){return k.split("::")[0]||k;}
function _wmPatGetWfr(k){return k.split("::")[1]||k;}
function _wmPatGetProg(k){return k.split("::")[2]||"";}
function _wmPatAllLots(){var s=new Set();Object.keys(WM_PAT.wafers).forEach(function(k){s.add(_wmPatGetLot(k));});return Array.from(s);}
function _wmPatMatchLots(k){if(!_wmPatCurLots)return true;var lot=_wmPatGetLot(k);for(var i=0;i<_wmPatCurLots.length;i++){if(_wmPatCurLots[i]===lot)return true;}return false;}
function _wmRetInfoFor(pk){
  var pfx=(WM_PAT.wafers[pk]||{}).pfx||"";
  var m=WM_PAT.retMaps&&WM_PAT.retMaps[pfx];
  return m||{retMap:WM_PAT.retMap,retShots:WM_PAT.retShots,retSiteTotals:WM_PAT.retSiteTotals};
}

function wmPatBinToggle(ibk,on){
  if(_wmPatBinChecked===null){
    _wmPatBinChecked=new Set();
    document.querySelectorAll("#wpa-binrow input[data-ib]").forEach(function(inp){if(inp.checked)_wmPatBinChecked.add(inp.dataset.ib);});
  }
  if(on){_wmPatBinChecked.add(String(ibk));}else{_wmPatBinChecked.delete(String(ibk));}
  _wmPatRender();
}
function _wmPatToggleBinAll(on){_wmPatBinChecked=on?null:new Set();_wmPatRender();}

function wmPatWaferToggle(pk,on){
  if(_wmPatSelWafers===null){
    _wmPatSelWafers=new Set(Object.keys(WM_PAT.wafers).filter(_wmPatMatchLots));
  }
  if(on)_wmPatSelWafers.add(pk);else _wmPatSelWafers.delete(pk);
  _wmPatRender();
}
function _wmPatLotAll(){_wmPatCurLots=null;_wmPatSelWafers=null;_wmPatBuildLotPicker();_wmPatRender();}
function _wmPatLotNone(){_wmPatCurLots=[];_wmPatSelWafers=new Set();_wmPatBuildLotPicker();_wmPatRender();}
function _wmPatWaferAll(){_wmPatSelWafers=null;_wmPatBuildWaferPicker();_wmPatRender();}
function _wmPatWaferNone(){_wmPatSelWafers=new Set();_wmPatBuildWaferPicker();_wmPatRender();}

function _wmPatLotToggle(lt,on){
  if(!_wmPatCurLots)_wmPatCurLots=_wmPatAllLots().slice();
  if(on){if(_wmPatCurLots.indexOf(lt)<0)_wmPatCurLots.push(lt);}
  else{_wmPatCurLots=_wmPatCurLots.filter(function(x){return x!==lt;});}
  if(!on&&_wmPatSelWafers){
    var rem=[];_wmPatSelWafers.forEach(function(pk){if(_wmPatGetLot(pk)===lt)rem.push(pk);});
    rem.forEach(function(pk){_wmPatSelWafers.delete(pk);});
  }
  _wmPatBuildWaferPicker();_wmPatRender();
}

function _wmPatBuildLotPicker(){
  var el=document.getElementById("wpa-lot-picker");if(!el)return;
  var all=_wmPatAllLots();
  if(all.length<=1){el.innerHTML="";_wmPatBuildWaferPicker();return;}
  var h='<span style="font-size:11px;font-weight:bold;color:#d7bde2;margin-right:4px">Lots:</span>';
  h+='<span style="font-size:10px;color:#bb8fce;cursor:pointer;text-decoration:underline;margin-right:6px" onclick="_wmPatLotAll()">All</span>';
  h+='<span style="font-size:10px;color:#bb8fce;cursor:pointer;text-decoration:underline;margin-right:8px" onclick="_wmPatLotNone()">None</span>';
  all.forEach(function(lt){
    var on=!_wmPatCurLots||_wmPatCurLots.indexOf(lt)>=0;
    h+='<label style="font-size:11px;color:#fff;margin-right:6px;cursor:pointer"><input type="checkbox" data-lot="'+lt+'" '+(on?"checked ":"")+'onchange="_wmPatLotToggle(this.dataset.lot,this.checked)" style="margin-right:2px">'+lt+'</label>';
  });
  el.innerHTML=h;
  _wmPatBuildWaferPicker();
}

function _wmPatBuildWaferPicker(){
  var wp=document.getElementById("wpa-wafer-picker");if(!wp)return;
  var all=_wmPatAllLots();
  var activeLots=_wmPatCurLots||all;
  var wKeys=Object.keys(WM_PAT.wafers).sort(function(a,b){
    var la=_wmPatGetLot(a),lb=_wmPatGetLot(b);if(la!==lb)return la<lb?-1:1;
    var wa=parseInt(_wmPatGetWfr(a))||0,wb=parseInt(_wmPatGetWfr(b))||0;return wa-wb;
  });
  var h='<span style="font-size:11px;font-weight:bold;color:#85c1e9;margin-right:4px;flex-shrink:0">Wafers:</span>';
  h+='<span style="font-size:10px;color:#85c1e9;cursor:pointer;text-decoration:underline;margin-right:4px" onclick="_wmPatWaferAll()">All</span>';
  h+='<span style="font-size:10px;color:#85c1e9;cursor:pointer;text-decoration:underline;margin-right:8px" onclick="_wmPatWaferNone()">None</span>';
  var prevLot="";
  wKeys.forEach(function(pk){
    var lt=_wmPatGetLot(pk),wn=_wmPatGetWfr(pk);
    if(activeLots.indexOf(lt)<0)return;
    if(lt!==prevLot){
      if(prevLot)h+='<span style="border-left:1px solid #555;margin:0 4px;height:14px;display:inline-block"></span>';
      h+='<span style="font-size:10px;color:#aeb6bf;margin-right:2px">['+lt+']</span>';
      prevLot=lt;
    }
    var on=_wmPatSelWafers===null||_wmPatSelWafers.has(pk);
    h+='<label style="font-size:11px;color:#d5d8dc;margin-right:4px;cursor:pointer"><input type="checkbox" data-pk="'+pk+'" '+(on?"checked ":"")+'onchange="wmPatWaferToggle(this.dataset.pk,this.checked)" style="margin-right:1px">W'+wn+'</label>';
  });
  wp.innerHTML=h;
}

function _wmPatBuildBinRow(ibArr){
  var br=document.getElementById("wpa-binrow");if(!br||!ibArr.length){if(br)br.innerHTML="";return;}
  var h='<span style="font-size:11px;font-weight:bold;color:#5d6d7e;flex-shrink:0;margin-right:4px">IB Filter:</span>';
  ibArr.forEach(function(ibk){
    var col=_wmIbColor(ibk);
    var on=_wmPatBinChecked===null||_wmPatBinChecked.has(String(ibk));
    h+='<label style="display:inline-flex;align-items:center;gap:3px;font-size:11px;cursor:pointer;margin-right:5px">'
      +'<span style="width:10px;height:10px;border-radius:2px;background:'+col+';display:inline-block;flex-shrink:0"></span>'
      +'<input type="checkbox"'+(on?" checked":"")+' data-ib="'+ibk+'" onchange="wmPatBinToggle(+this.dataset.ib,this.checked)">IB'+ibk+'</label>';
  });
  h+='<span style="font-size:10px;color:#2471a3;cursor:pointer;text-decoration:underline;margin-left:6px" onclick="_wmPatToggleBinAll(true)">All</span>';
  h+='<span style="font-size:10px;color:#2471a3;cursor:pointer;text-decoration:underline;margin-left:4px" onclick="_wmPatToggleBinAll(false)">None</span>';
  br.innerHTML=h;
}

function _wmSetEdgeRows(n){
  _wmEdgeExcRows=+n;
  document.querySelectorAll("select.wm-edge-sel").forEach(function(s){s.value=String(_wmEdgeExcRows);});
  _wmPatRender();
}

/* wmShowPatLot(lot) — open WPA pre-filtered to a lot (called from outside) */
function wmShowPatLot(lots){
  if(typeof lots==="string")lots=[lots];
  _wmPatCurLots=lots;_wmPatSelWafers=null;_wmPatBinChecked=null;_wmPatRetUnchecked=null;
  _wmPatBuildLotPicker();
  var ov=document.getElementById("wpa-overlay");
  if(ov)ov.classList.add("open");
  _wmPatRender();
  _wpaInitDrag();
}
function wpaClose(){
  var ov=document.getElementById("wpa-overlay");
  if(ov)ov.classList.remove("open");
}
function wpaOpen(){
  var ov=document.getElementById("wpa-overlay");
  if(ov){ov.classList.add("open");_wmPatRender();_wpaInitDrag();}
}

function _wpaInitDrag(){
  var drag=document.getElementById("wpa-drag");var box=document.getElementById("wpa-box");
  if(!drag||!box||drag._wpaInitDone)return;
  drag._wpaInitDone=true;
  var ox=0,oy=0,bx=0,by=0;
  drag.addEventListener("mousedown",function(e){
    bx=box.getBoundingClientRect().left;by=box.getBoundingClientRect().top;
    ox=e.clientX;oy=e.clientY;
    function mv(e2){box.style.left=(bx+e2.clientX-ox)+"px";box.style.top=(by+e2.clientY-oy)+"px";}
    function up(){document.removeEventListener("mousemove",mv);document.removeEventListener("mouseup",up);}
    document.addEventListener("mousemove",mv);document.addEventListener("mouseup",up);
  });
}

/* Core render — wafer maps + pattern score table */
function _wmPatRender(){
  var maps=document.getElementById("wpa-maps");
  var tbody=document.getElementById("wpa-tbody");
  var ltEl=document.getElementById("wpa-lot-trend");
  if(!maps||!tbody)return;
  var allKeys=Object.keys(WM_PAT.wafers).filter(function(k){return _wmPatMatchLots(k);}).sort(function(a,b){
    var la=_wmPatGetLot(a),lb=_wmPatGetLot(b);if(la!==lb)return la<lb?-1:1;
    return(parseInt(_wmPatGetWfr(a))||0)-(parseInt(_wmPatGetWfr(b))||0);
  });
  var keys=_wmPatSelWafers===null?allKeys:allKeys.filter(function(k){return _wmPatSelWafers.has(k);});
  var FIXED_W=190,pad=2;
  var mapsHtml="",tbHtml="",ibSeen={},sc_acc={};
  var _bar=function(v){
    var pw=Math.round(v*90);
    var c=v<0.35?"#27ae60":v<0.65?"#e67e22":"#c0392b";
    return'<span style="background:#e8e8e8;border-radius:3px;height:8px;width:90px;display:inline-block;vertical-align:middle">'
      +'<span style="height:8px;border-radius:3px;display:block;width:'+pw+'px;background:'+c+'"></span></span>'
      +'<span style="font-size:10px;color:#555;margin-left:3px">'+Math.round(v*100)+'%</span>';
  };

  keys.forEach(function(pk){
    var wdata=WM_PAT.wafers[pk];
    var dies=wdata&&wdata.dies?wdata.dies:wdata;
    var mLot=wdata.lot||_wmPatGetLot(pk);
    var mWfr=wdata.wafer||_wmPatGetWfr(pk);
    var mMat=wdata.material||"";
    if(!dies||!dies.length){
      mapsHtml+='<div style="text-align:center"><div style="font-size:11px;font-weight:bold;color:#aaa">'+mLot+' W'+mWfr+'</div><div style="font-size:10px;color:#ccc;margin-top:4px">no data</div></div>';
      tbHtml+='<tr><td>'+mLot+'</td><td>W'+mWfr+'</td><td colspan="9" style="color:#bbb;font-size:10px">no data</td></tr>';
      return;
    }
    var xs=[],ys=[];
    dies.forEach(function(d){if(d[0]!==null){xs.push(d[0]);ys.push(d[1]);}});
    var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
    var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
    var cs=Math.max(2,(FIXED_W-pad*2)/(xMax-xMin+1));
    var xSpan=xMax-xMin,ySpan=yMax-yMin;
    var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
    var W=FIXED_W,H=Math.round((yMax-yMin+1)*csy+pad*2);
    var xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;
    var xRad=(xMax-xMin)/2||1,yRad=(yMax-yMin)/2||1;
    var failXn=[],failYn=[],failActX=[],failActY=[],totalDies=0,failDies=0;
    var rects=[];
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2];if(x===null)return;
      totalDies++;
      var px=(pad+(x-xMin)*cs).toFixed(1),py=(pad+(yMax-y)*csy).toFixed(1);
      var ibKey=(ib!==null&&ib!==undefined)?ib:null;
      var fill=_wmIbColor(ibKey);ibSeen[String(ibKey)]=fill;
      var binOn=(_wmPatBinChecked===null||_wmPatBinChecked.has(String(ibKey)));
      var opacity=binOn?"1":"0.08";
      if(_wmIsFail(ibKey)&&ibKey!==null&&binOn){
        var xn=(x-xCtr)/xRad,yn=(y-yCtr)/yRad;
        var _isEdge=(_wmEdgeExcRows>0&&(x<xMin+_wmEdgeExcRows||x>xMax-_wmEdgeExcRows||y<yMin+_wmEdgeExcRows||y>yMax-_wmEdgeExcRows));
        if(_isEdge){opacity="0.15";}else{failXn.push(xn);failYn.push(yn);failActX.push(x);failActY.push(y);failDies++;}
      }
      rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'" height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" opacity="'+opacity+'"/>');
    });

    // Reticle shot overlay
    var _pkRetInfo=_wmRetInfoFor(pk);
    var _pkShots=(_pkRetInfo.retShots&&_pkRetInfo.retShots.length)?_pkRetInfo.retShots:(WM_PAT.retShots||[]);
    var retOut="";
    _pkShots.forEach(function(s,si){
      var sx=(pad+(s[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-s[3])*csy).toFixed(1);
      var sw=((s[2]-s[0]+1)*cs).toFixed(1),sh=((s[3]-s[1]+1)*csy).toFixed(1);
      retOut+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#2471a3" stroke-width="0.7" opacity="0.35"/>';
    });

    var sc={center:0,edge:0,donut:0,systematic:0,reticle:0,random:0};
    var _psc={confidence:"LOW"};
    if(failDies>=3){
      _psc=_wmScorePattern(failXn,failYn);
      sc.center=_psc.center;sc.edge=_psc.edge;sc.donut=_psc.donut;sc.systematic=_psc.systematic;
      if(WM_PAT.hasReticle&&failActX.length>0){
        var _wri2=_wmRetInfoFor(pk);
        sc.reticle=_wmScoreReticle(failActX,failActY,_wri2.retMap||WM_PAT.retMap,_wri2.retSiteTotals||WM_PAT.retSiteTotals);
      }
      var dominated=Math.max(sc.center,sc.edge,sc.donut,sc.systematic,sc.reticle);
      sc.random=Math.max(0,1-dominated);
    } else {
      sc.random=failDies>0?1:0;
    }
    sc_acc[pk]={center:sc.center,edge:sc.edge,donut:sc.donut,systematic:sc.systematic,reticle:sc.reticle,random:sc.random,failDies:failDies};

    var primary="RANDOM",pCol=_pColors.RANDOM,bestScore=sc.random;
    ["center","edge","donut","systematic","reticle"].forEach(function(d){if((sc[d]||0)>bestScore){bestScore=sc[d];primary=d.toUpperCase();pCol=_pColors[d.toUpperCase()]||"#555";}});
    var _confCol={HIGH:"#27ae60",MEDIUM:"#e67e22",LOW:"#e74c3c"}[_psc.confidence]||"#999";
    var failPct=totalDies>0?(failDies/totalDies*100).toFixed(1)+"%":"0%";

    var clipId="wmpc_"+pk.replace(/[^a-z0-9]/gi,"_");
    var cx=(pad+(xCtr-xMin)*cs+cs*0.45).toFixed(1);
    var cy=(pad+(yMax-yCtr)*csy+csy*0.45).toFixed(1);
    var rx=(xRad*cs+cs*0.5).toFixed(1);
    var ry=(yRad*csy+csy*0.5).toFixed(1);
    var svgStr='<svg width="'+W+'" height="'+H+'" style="display:block;margin:0 auto">'
      +'<defs><clipPath id="'+clipId+'"><ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry+'"/></clipPath></defs>'
      +'<g clip-path="url(#'+clipId+')">'+rects.join("")+retOut+'</g>'
      +'<ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry+'" fill="none" stroke="#bdc3c7" stroke-width="1.5"/></svg>';

    mapsHtml+='<div style="text-align:center">'+svgStr
      +'<div style="font-size:10px;color:'+pCol+';font-weight:bold;margin-top:2px">'+primary+'</div>'
      +'<div style="font-size:11px;font-weight:bold;color:#2c3e50;margin-top:2px">'+mLot+' W'+mWfr+'</div>'
      +'</div>';

    tbHtml+='<tr>'
      +'<td style="font-size:10px;white-space:nowrap">'+mLot+'</td>'
      +'<td style="font-weight:bold;white-space:nowrap">W'+mWfr+'</td>'
      +'<td style="font-size:10px;color:#555">'+mMat+'</td>'
      +'<td style="font-weight:bold;color:'+pCol+'">'+primary+'</td>'
      +'<td style="color:'+_confCol+';font-size:10px">'+_psc.confidence+'</td>'
      +'<td>'+failPct+'<span style="font-size:9px;color:#999;margin-left:2px">(n='+failDies+')</span></td>'
      +'<td>'+_bar(sc.center||0)+'</td>'
      +'<td>'+_bar(sc.edge||0)+'</td>'
      +'<td>'+_bar(sc.donut||0)+'</td>'
      +'<td>'+_bar(sc.systematic||0)+'</td>'
      +(WM_PAT.hasReticle?'<td>'+_bar(sc.reticle||0)+'</td>':'')
      +'<td>'+_bar(sc.random||0)+'</td></tr>';
  });

  maps.innerHTML=mapsHtml||'<span style="color:#999;font-size:12px">No wafers selected</span>';
  tbody.innerHTML=tbHtml;

  // Lot trend summary
  if(ltEl){
    var _lta={};
    keys.forEach(function(pk){
      var lot=_wmPatGetLot(pk);
      if(!_lta[lot])_lta[lot]={n:0,center:0,edge:0,donut:0,systematic:0,reticle:0,random:0};
      var _s=_lta[lot];_s.n++;
      if(sc_acc[pk]){_s.center+=sc_acc[pk].center;_s.edge+=sc_acc[pk].edge;_s.donut+=sc_acc[pk].donut;
        _s.systematic+=sc_acc[pk].systematic;_s.reticle+=sc_acc[pk].reticle;_s.random+=sc_acc[pk].random;}
    });
    var _lots=Object.keys(_lta).sort();
    if(_lots.length>1){
      var _lth='<table style="width:100%;border-collapse:collapse;font-size:11px"><thead><tr style="background:#d6eaf8">'
        +'<th style="text-align:left;padding:2px 6px;color:#1a5276">Lot</th><th style="padding:2px 4px;color:#555">Wfrs</th>'
        +'<th style="padding:2px 6px;color:#555">Primary</th><th style="padding:2px 4px;color:#c0392b">Center</th>'
        +'<th style="padding:2px 4px;color:#e67e22">Edge</th><th style="padding:2px 4px;color:#8e44ad">Donut</th>'
        +'<th style="padding:2px 4px;color:#2471a3">Systematic</th>'
        +(WM_PAT.hasReticle?'<th style="padding:2px 4px;color:#1f618d">Reticle</th>':'')
        +'<th style="padding:2px 4px;color:#27ae60">Random</th></tr></thead><tbody>';
      _lots.forEach(function(lot,li){
        var a=_lta[lot],n=a.n||1;
        var lsc={center:a.center/n,edge:a.edge/n,donut:a.donut/n,systematic:a.systematic/n,reticle:a.reticle/n,random:a.random/n};
        var lPrim='RANDOM',lCol=_pColors.RANDOM,lV=lsc.random;
        ['center','edge','donut','systematic','reticle'].forEach(function(d){if((lsc[d]||0)>lV){lV=lsc[d];lPrim=d.toUpperCase();lCol=_pColors[d.toUpperCase()]||'#555';}});
        var bg=li%2?'background:#f7f9fc':'';
        _lth+='<tr style="'+bg+'"><td style="padding:2px 6px;font-weight:bold;color:#1a5276">'+lot+'</td>'
          +'<td style="text-align:center;padding:2px 4px">'+a.n+'</td>'
          +'<td style="font-weight:bold;color:'+lCol+';padding:2px 6px">'+lPrim+'</td>'
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.center*100)+'%</td>'
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.edge*100)+'%</td>'
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.donut*100)+'%</td>'
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.systematic*100)+'%</td>'
          +(WM_PAT.hasReticle?'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.reticle*100)+'%</td>':'')
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.random*100)+'%</td></tr>';
      });
      _lth+='</tbody></table>';
      ltEl.innerHTML=_lth;
    } else {ltEl.innerHTML="";}
  }

  // Rebuild IB filter row
  var ibKeys=[];
  Object.keys(ibSeen).forEach(function(k){if(k!=="null"&&k!=="undefined")ibKeys.push(+k);});
  ibKeys.sort(function(a,b){return a-b;});
  _wmPatBuildBinRow(ibKeys);
}
"""

# Full JS — complete WPA (wm-pat-* IDs, matches yield-dashboard _pipeline_html.py)
# The old WPA_SCORE_JS / WPA_PICKER_JS above are superseded; WPA_FULL_JS is authoritative.
WPA_FULL_JS = r"""
var _wmSel=new Map();
var _wmFtDdState={};
var _wmFtDdOpen_=null;
var _wmCurPatkey=null;
var _wmCurLot=null;

function _wmIbColor(ib){
  if(ib===null||ib===undefined)return"#e0e0e0";
  var c=WM_PAT.ibColors&&WM_PAT.ibColors[String(parseInt(ib))];
  return c||"#aaaaaa";
}
var _wmFailThr=3;var _wmEdgeExcRows=1;
function _wmIsFail(ib){if(ib===null||ib===undefined)return false;var n=parseInt(ib);return n>=_wmFailThr;}
function _wmSetFailThr(v){_wmFailThr=+v;var sel=document.getElementById("wm-fail-thr");if(sel)sel.value=v;document.querySelectorAll("input[name='wm-thr-rb']").forEach(function(rb){rb.checked=(+rb.value===_wmFailThr);});if(typeof _wmPatRender==="function")_wmPatRender();if(typeof wmPatRenderReticle==="function")wmPatRenderReticle();}
function _wmSetEdgeRows(n){_wmEdgeExcRows=+n;_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);document.querySelectorAll("select.wm-edge-sel").forEach(function(s){s.value=String(_wmEdgeExcRows);});}
function _wmScorePattern(failXn,failYn){
  var N=failXn.length;
  if(!N)return{center:0,edge:0,donut:0,systematic:0,random:1,confidence:"LOW"};
  var B1=0,B2=0,B3=0,B4=0,B5=0,B6=0,q=[0,0,0,0];
  for(var i=0;i<N;i++){
    var r=Math.sqrt(failXn[i]*failXn[i]+failYn[i]*failYn[i]);
    if(r<0.15)B1++;else if(r<0.40)B2++;else if(r<0.60)B3++;else if(r<0.75)B4++;else if(r<0.90)B5++;else B6++;
    var xi=failXn[i],yi=failYn[i];
    if(xi>=0&&yi>=0)q[0]++;else if(xi<0&&yi>=0)q[1]++;else if(xi<0&&yi<0)q[2]++;else q[3]++;
  }
  var fC=(B1+B2)/N,eC=0.16;
  var fE=(B5+B6)/N,eE=0.4375;
  var fM=(B3+B4)/N,eM=0.4025;
  var centerScore=Math.max(0,Math.min(1,(fC-eC)/(1-eC)));
  var edgeScore  =Math.max(0,Math.min(1,(fE-eE)/(1-eE)));
  var midEnrich  =Math.max(0,(fM-eM)/(1-eM));
  var donutScore =Math.min(1,midEnrich*2*(1-Math.max(centerScore,edgeScore)*0.7));
  var sampleConf =Math.min(1,N/20);
  var qImbal=(Math.max.apply(null,q)-Math.min.apply(null,q))/N;
  var systematicScore=Math.min(1,qImbal*2.5)*sampleConf;
  var dominated=Math.max(centerScore,edgeScore,donutScore,systematicScore);
  var randomScore=Math.max(0,Math.min(1,1-dominated));
  var conf=N<20?"LOW":N<50?"MEDIUM":"HIGH";
  return{center:+centerScore.toFixed(2),edge:+edgeScore.toFixed(2),donut:+donutScore.toFixed(2),systematic:+systematicScore.toFixed(2),random:+randomScore.toFixed(2),confidence:conf};
}
function _wmScoreReticle(actX,actY,rm,st){
  if(!rm)rm=WM_PAT.retMap;if(!st)st=WM_PAT.retSiteTotals;
  if(!rm||!st||!actX||!actX.length)return 0;
  var siteShots={},siteCnt={},N=actX.length;
  for(var i=0;i<N;i++){var info=rm[actX[i]+","+actY[i]];if(!info)continue;var sk=info[0]+","+info[1];var si=String(info[2]);if(!siteShots[sk]){siteShots[sk]={};siteCnt[sk]=0;}siteShots[sk][si]=true;siteCnt[sk]++;}
  var sites=Object.keys(siteShots);if(!sites.length)return 0;
  var maxSiteScore=0,weightedSum=0,totalMapped=0;
  sites.forEach(function(sk){var totShots=st[sk]||1;var failShots=Object.keys(siteShots[sk]).length;var score=failShots/totShots;var cnt=siteCnt[sk];totalMapped+=cnt;weightedSum+=score*cnt;if(score>maxSiteScore)maxSiteScore=score;});
  if(!totalMapped)return 0;
  var raw=(weightedSum/totalMapped)*0.4+maxSiteScore*0.6;
  var sampleConf=Math.min(1,N/15);
  return Math.min(1,raw*sampleConf);
}
function _wmPrimary(sc){
  var best="random",bv=sc.random;
  ["center","edge","donut","systematic","reticle"].forEach(function(k){if(sc[k]!==undefined&&sc[k]>bv){bv=sc[k];best=k;}});
  return{center:"CENTER",edge:"EDGE",donut:"DONUT",systematic:"SYSTEMATIC",reticle:"RETICLE",random:"RANDOM"}[best]||best.toUpperCase();
}
var _pColors={CENTER:"#c0392b",EDGE:"#e67e22",DONUT:"#8e44ad",SYSTEMATIC:"#2471a3",RETICLE:"#1f618d",RANDOM:"#27ae60"};
var _wmPatBinChecked=null;
var _wmmHlIb=null;
var _wmmHeatMode=false;
var _wmPatSelWafers=null;
var _wmPatCurLots=null;
var _wmPatCurProgs=null;
var _wmPatRetUnchecked=null;
var _wmTileW=190;
function _wmSetTileW(w){_wmTileW=w;var lbl=document.getElementById("wm-tile-size-lbl");if(lbl)lbl.textContent=w+"px";var sl=document.getElementById("wm-tile-size-slider");if(sl)sl.value=w;_wmPatRender();}
var _wmPatSiteToShots=null;
function _wmPatGetLot(k){return k.split("::")[0]||k;}
function _wmPatGetWfr(k){return k.split("::")[1]||k;}
function _wmPatGetProg(k){return k.split("::")[2]||"";}  
function _wmPatAllLots(){var s=new Set();Object.keys(WM_PAT.wafers).forEach(function(k){s.add(_wmPatGetLot(k));});return Array.from(s);}
function _wmPatAllProgs(){var s=new Set();Object.keys(WM_PAT.wafers).forEach(function(k){var p=_wmPatGetProg(k);if(p)s.add(p);});return Array.from(s).sort();}
function _wmPatMatchLots(k){if(!_wmPatCurLots)return true;var lot=_wmPatGetLot(k);for(var i=0;i<_wmPatCurLots.length;i++){if(_wmPatCurLots[i]===lot)return true;}return false;}
function _wmPatMatchProgs(k){if(!_wmPatCurProgs)return true;var p=_wmPatGetProg(k);if(!p)return true;for(var i=0;i<_wmPatCurProgs.length;i++){if(_wmPatCurProgs[i]===p)return true;}return false;}
function _wmRetInfoFor(pk){var pfx=(WM_PAT.wafers[pk]||{}).pfx||"";var m=WM_PAT.retMaps&&WM_PAT.retMaps[pfx];return m||{retMap:WM_PAT.retMap,retShots:WM_PAT.retShots,retSiteTotals:WM_PAT.retSiteTotals};}
var _wmPatIsPopup=false;
var _wmPatPopupWin=null;
function wmOpenPat(){
  var ov=document.getElementById("wm-pat-overlay");
  if(ov){ov.classList.add("open");_wmPatBuildLotPicker();_wmPatRender();_wmPatInitDrag();}
}
function wmHidePat(){
  if(_wmPatIsPopup){window.close();return;}
  var ov=document.getElementById("wm-pat-overlay");
  if(ov)ov.classList.remove("open");
}
function wmPatTab(t){
  ["impact","composite2","reticle","guide"].forEach(function(n){
    var btn=document.getElementById("wm-pat-tab-"+n);
    var pane=document.getElementById("wm-pat-pane-"+n);
    if(btn)btn.classList.toggle("on",n===t);
    if(pane)pane.classList.toggle("on",n===t);
  });
  if(t==="reticle")wmPatRenderReticle();
}
function wmPatRenderReticle(){
  var el=document.getElementById("wm-pat-reticle-body");
  if(!el)return;
  if(!WM_PAT.hasReticle||(!WM_PAT.retMap&&(!WM_PAT.retMaps||!Object.keys(WM_PAT.retMaps).length))){el.innerHTML='<span style="color:#aaa;font-size:11px">No reticle mapping loaded.</span>';return;}
  var allKeys=Object.keys(WM_PAT.wafers).filter(function(k){return _wmPatMatchLots(k)&&_wmPatMatchProgs(k);});
  var keys=_wmPatSelWafers===null?allKeys:allKeys.filter(function(k){return _wmPatSelWafers.has(k);});
  var nWafers=keys.filter(function(pk){var w=WM_PAT.wafers[pk];return w&&w.dies&&w.dies.length;}).length;
  var _retAllX=[],_retAllY=[];keys.forEach(function(pk){var _w=WM_PAT.wafers[pk];var _d=_w&&_w.dies?_w.dies:_w;if(_d)_d.forEach(function(d){if(d[0]!==null){_retAllX.push(d[0]);_retAllY.push(d[1]);}});});
  var _retXMin=_retAllX.length?Math.min.apply(null,_retAllX):0,_retXMax=_retAllX.length?Math.max.apply(null,_retAllX):0;
  var _retYMin=_retAllY.length?Math.min.apply(null,_retAllY):0,_retYMax=_retAllY.length?Math.max.apply(null,_retAllY):0;
  var siteFailShots={},siteFailCount={},grandTotalFail=0;
  var shotFailData={},shotWaferHits={};
  keys.forEach(function(pk){
    var wdata=WM_PAT.wafers[pk];
    var dies=wdata&&wdata.dies?wdata.dies:wdata;
    if(!dies||!dies.length)return;
    var _shotsSeen={};
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2];if(x===null||x===undefined)return;
      var _ib2=typeof ib==="number"?ib:parseInt(ib);if(isNaN(_ib2))return;
      var binOn=(_wmPatBinChecked===null||_wmPatBinChecked.has(String(ib)));
      if(!binOn)return;
      if(_wmPatBinChecked===null&&!_wmIsFail(_ib2))return;
      var _lri=_wmRetInfoFor(pk);var info=_lri.retMap&&_lri.retMap[x+","+y];if(!info)return;
      var sk=info[0]+","+info[1];var shotIdx=String(info[2]);
      if(_wmEdgeExcRows>0&&(x<_retXMin+_wmEdgeExcRows||x>_retXMax-_wmEdgeExcRows||y<_retYMin+_wmEdgeExcRows||y>_retYMax-_wmEdgeExcRows))return;
      if(!siteFailShots[sk])siteFailShots[sk]={};
      if(!siteFailShots[sk][pk])siteFailShots[sk][pk]=new Set();
      siteFailShots[sk][pk].add(shotIdx);
      siteFailCount[sk]=(siteFailCount[sk]||0)+1;
      grandTotalFail++;
      if(!shotFailData[shotIdx])shotFailData[shotIdx]={cnt:0,sites:{}};
      shotFailData[shotIdx].cnt++;
      shotFailData[shotIdx].sites[sk]=true;
      if(!_shotsSeen[shotIdx]){_shotsSeen[shotIdx]=true;shotWaferHits[shotIdx]=(shotWaferHits[shotIdx]||0)+1;}
    });
  });
  var _lrsl=(keys.length?_wmRetInfoFor(keys[0]):{}).retSiteLabels||WM_PAT.retSiteLabels||{};
  var _lrst=(keys.length?_wmRetInfoFor(keys[0]):{}).retSiteTotals||WM_PAT.retSiteTotals||{};
  var _siteNum=WM_PAT._retSiteNum||{};
  var sites=Object.keys(siteFailCount);
  if(!sites.length){el.innerHTML='<span style="color:#7f8c8d;font-size:11px">No fail dies mapped to reticle sites for selected wafers/bins.</span>';return;}
  sites.sort(function(a,b){return siteFailCount[b]-siteFailCount[a];});
  var _clrLeg='<div style="margin-top:5px;font-size:10px;color:#888"><b>Color:</b> <span style="background:#fde8e8;padding:1px 4px;border-radius:2px">Red \u226570% hit</span> &nbsp; <span style="background:#fef3cd;padding:1px 4px;border-radius:2px">Yellow 40\u201369%</span></div>';
  var _clrLink=(_wmPatRetUnchecked&&_wmPatRetUnchecked.size>0?'<div style="margin-bottom:4px"><a href="#" onclick="wmPatRetClear();return false" style="color:#c0392b;font-weight:bold;font-size:10px">\u00d7 Clear highlights</a></div>':'');
  var h='<style>.wmret th{border-right:1px solid rgba(255,255,255,0.35);border-bottom:1px solid rgba(255,255,255,0.2)}.wmret td{border-right:1px solid #c8d8e8;border-bottom:1px solid #e8eef4}.wmret th:last-child,.wmret td:last-child{border-right:none}</style><div style="font-weight:bold;font-size:11px;color:#1f618d;margin:4px 0 2px;padding-bottom:2px;border-bottom:2px solid #1f618d">\u25a3 Table A \u2014 By Reticle Die Loc</div>';
  h+=_clrLink;
  h+='<table class="wmret" style="border-collapse:collapse;font-size:11px;width:auto;white-space:nowrap;display:block;margin-left:0"><thead><tr>';
  h+='<th style="background:#1f618d;color:#fff;padding:2px 4px;text-align:center" title="Highlight on map">\u2611</th>';
  h+='<th style="background:#1f618d;color:#fff;padding:2px 4px">Loc #</th>';
  h+='<th style="background:#1f618d;color:#fff;padding:2px 4px">RX</th>';
  h+='<th style="background:#1f618d;color:#fff;padding:2px 4px">RY</th>';
  h+='<th style="background:#1f618d;color:#fff;padding:2px 4px">Fail Dies</th>';
  h+='<th style="background:#1f618d;color:#fff;padding:2px 4px">%</th>';
  h+='<th style="background:#1f618d;color:#fff;padding:2px 4px">Wafer Hits</th>';
  h+='<th style="background:#1f618d;color:#fff;padding:2px 4px">Hit%</th>';
  h+='<th style="background:#1f618d;color:#fff;padding:2px 4px">Shots/wfr</th></tr></thead><tbody>';
  var altRow=false;
  sites.forEach(function(sk){
    var parts=sk.split(",");var rx=parts[0],ry=parts[1];
    var locNum=_siteNum[sk]||(_lrsl[sk]!=null?_lrsl[sk]:"-");
    var fc=siteFailCount[sk];
    var pctF=grandTotalFail>0?(fc/grandTotalFail*100).toFixed(1):"0.0";
    var waferHits=Object.keys(siteFailShots[sk]).length;
    var hitPct=nWafers>0?(waferHits/nWafers*100).toFixed(0):0;
    var heatPct=nWafers>0?waferHits/nWafers:0;
    var totShots=(_lrst[sk])||1;
    var bg=heatPct>=0.7?"#fde8e8":heatPct>=0.4?"#fef3cd":altRow?"#f0f4fb":"#fff";
    var isChk=!(_wmPatRetUnchecked&&_wmPatRetUnchecked.has(sk));
    var dimRow=_wmPatRetUnchecked&&_wmPatRetUnchecked.has(sk);
    h+='<tr style="background:'+bg+';'+(dimRow?"opacity:0.3":"")+'">'; 
    h+='<td style="padding:1px 4px;text-align:center"><input type="checkbox" data-sk="'+sk+'" '+(isChk?'checked ':' ')+'onchange="wmPatRetSiteToggle(this.dataset.sk,this.checked)"></td>';
    h+='<td style="padding:1px 4px;text-align:center;font-weight:bold;color:#1a5276">'+locNum+'</td>';
    h+='<td style="padding:1px 4px;text-align:center">'+rx+'</td>';
    h+='<td style="padding:1px 4px;text-align:center">'+ry+'</td>';
    h+='<td style="padding:1px 4px;text-align:right">'+fc+'</td>';
    h+='<td style="padding:1px 4px;text-align:right">'+pctF+'%</td>';
    h+='<td style="padding:1px 4px;text-align:right">'+waferHits+'/'+nWafers+'</td>';
    h+='<td style="padding:1px 4px;text-align:right;font-weight:'+(heatPct>=0.7?"bold":"normal")+';color:'+(heatPct>=0.7?"#c0392b":heatPct>=0.4?"#e67e22":"#27ae60")+'">'+( +hitPct)+'%</td>';
    h+='<td style="padding:1px 4px;text-align:right;color:#888">'+totShots+'</td></tr>';
    altRow=!altRow;
  });
  h+='</tbody></table>'+_clrLeg;
  var shots=Object.keys(shotFailData).sort(function(a,b){return shotFailData[b].cnt-shotFailData[a].cnt;});
  var _clrShotLink=(_wmPatShotUnchecked&&_wmPatShotUnchecked.size>0?'<div style="margin-bottom:4px"><a href="#" onclick="_wmPatToggleShotAll(true);return false" style="color:#c0392b;font-weight:bold;font-size:10px">\u00d7 Clear shot filter</a></div>':'');
  h+='<div style="font-weight:bold;font-size:11px;color:#6c3483;margin:10px 0 2px;padding-bottom:2px;border-bottom:2px solid #6c3483">\u25a3 Table B \u2014 By Shot # (stage/scanner systematic)</div>';
  h+=_clrShotLink;
  h+='<table class="wmret" style="border-collapse:collapse;font-size:11px;width:auto;white-space:nowrap;display:block;margin-left:0"><thead><tr>';
  h+='<th style="background:#6c3483;color:#fff;padding:2px 4px;text-align:center" title="Show/hide on map">\u2611 <a href="#" onclick="_wmPatToggleShotAll(true);return false" style="color:#dcc6f0;font-size:9px;text-decoration:none">All</a> <a href="#" onclick="_wmPatToggleShotAll(false);return false" style="color:#dcc6f0;font-size:9px;text-decoration:none">None</a></th>';
  h+='<th style="background:#6c3483;color:#fff;padding:2px 4px">Shot #</th>';
  h+='<th style="background:#6c3483;color:#fff;padding:2px 4px">Fail Dies</th>';
  h+='<th style="background:#6c3483;color:#fff;padding:2px 4px">Die Locs</th>';
  h+='<th style="background:#6c3483;color:#fff;padding:2px 4px">Wafer Hits</th>';
  h+='<th style="background:#6c3483;color:#fff;padding:2px 4px">Hit%</th></tr></thead><tbody>';
  altRow=false;
  shots.forEach(function(si){
    var sd=shotFailData[si];
    var wh=shotWaferHits[si]||0;
    var hp=nWafers>0?(wh/nWafers*100).toFixed(0):0;
    var heatP=nWafers>0?wh/nWafers:0;
    var bg2=heatP>=0.7?"#f3e5f5":heatP>=0.4?"#ede7f6":altRow?"#f9f4fc":"#fff";
    var locNums=Object.keys(sd.sites).map(function(sk){return _siteNum[sk]||sk;}).sort(function(a,b){return(+a)-(+b);}).join(", ");
    var isShotOn=!(_wmPatShotUnchecked&&_wmPatShotUnchecked.has(+si));
    h+='<tr style="background:'+bg2+';'+(!isShotOn?"opacity:0.3":"")+'">'; 
    h+='<td style="padding:1px 4px;text-align:center"><input type="checkbox" data-si="'+si+'" '+(isShotOn?"checked ":"")+' onchange="_wmPatShotToggle(+this.dataset.si,this.checked)"></td>';
    h+='<td style="padding:1px 4px;text-align:center;font-weight:bold;color:#6c3483">Shot '+si+'</td>';
    h+='<td style="padding:1px 4px;text-align:right">'+sd.cnt+'</td>';
    h+='<td style="padding:1px 4px;font-size:10px;color:#555">Loc '+locNums+'</td>';
    h+='<td style="padding:1px 4px;text-align:right">'+wh+'/'+nWafers+'</td>';
    h+='<td style="padding:1px 4px;text-align:right;font-weight:'+(heatP>=0.7?"bold":"normal")+';color:'+(heatP>=0.7?"#c0392b":heatP>=0.4?"#9b59b6":"#27ae60")+'">'+( +hp)+'%</td></tr>';
    altRow=!altRow;
  });
  h+='</tbody></table><div style="margin-top:5px;font-size:10px;color:#888"><b>Color:</b> <span style="background:#f3e5f5;padding:1px 4px;border-radius:2px">Purple \u226570% hit</span> &nbsp; <span style="background:#ede7f6;padding:1px 4px;border-radius:2px">Light 40\u201369%</span></div>';
  el.innerHTML='<div style="float:left;text-align:left">'+h+'</div>';
}
function wmPatBinToggle(ibk,on){
  if(_wmPatBinChecked===null){
    _wmPatBinChecked=new Set();
    document.querySelectorAll("#wm-pat-binrow input[data-ib]").forEach(function(inp){if(inp.checked)_wmPatBinChecked.add(inp.dataset.ib);});
  }
  if(on){_wmPatBinChecked.add(String(ibk));}else{_wmPatBinChecked.delete(String(ibk));}
  _wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
function _wmPatToggleBinAll(on){_wmPatBinChecked=on?null:new Set();_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);}
function wmPatWaferToggle(pk,on){
  if(_wmPatSelWafers===null){
    var keys=Object.keys(WM_PAT.wafers).filter(function(k){return _wmPatMatchLots(k)&&_wmPatMatchProgs(k);});
    _wmPatSelWafers=new Set(keys);
  }
  if(on)_wmPatSelWafers.add(pk);else _wmPatSelWafers.delete(pk);
  _wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
function wmPatRetSiteToggle(sk,on){
  if(!on){if(!_wmPatRetUnchecked)_wmPatRetUnchecked=new Set();_wmPatRetUnchecked.add(sk);}
  else{if(_wmPatRetUnchecked){_wmPatRetUnchecked.delete(sk);if(_wmPatRetUnchecked.size===0)_wmPatRetUnchecked=null;}}
  _wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
function wmPatRetClear(){_wmPatRetUnchecked=null;_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);}
function wmShowPatLot(lots){
  if(typeof lots==="string")lots=[lots];
  _wmPatCurLots=lots;
  _wmPatCurProgs=null;
  _wmPatSelWafers=null;
  _wmPatBinChecked=null;
  _wmPatRetUnchecked=null;
  _wmPatBuildLotPicker();
  var ov=document.getElementById("wm-pat-overlay");
  if(ov)ov.classList.add("open");
  _wmPatRender();
  _wmPatInitDrag();
}
function _wmPatBuildProgPicker(){
  var el=document.getElementById("wm-pat-prog-picker");if(!el)return;
  var all=_wmPatAllProgs();
  if(all.length<=1){el.style.display="none";el.innerHTML="";return;}
  el.style.display="";
  var h='<span style="font-size:11px;font-weight:bold;color:#aed6f1;margin-right:4px">Programs:</span>';
  h+='<span style="font-size:10px;color:#7fb3d3;cursor:pointer;text-decoration:underline;margin-right:6px" onclick="_wmPatProgAll()">All</span>';
  h+='<span style="font-size:10px;color:#7fb3d3;cursor:pointer;text-decoration:underline;margin-right:8px" onclick="_wmPatProgNone()">None</span>';
  all.forEach(function(p){
    var on=!_wmPatCurProgs||_wmPatCurProgs.indexOf(p)>=0;
    h+='<label style="font-size:11px;color:#d6eaf8;margin-right:6px;cursor:pointer"><input type="checkbox" data-prog="'+p+'" '+(on?"checked ":"")+' onchange="_wmPatProgToggle(this.dataset.prog,this.checked)" style="margin-right:2px">'+p+'</label>';
  });
  el.innerHTML=h;
}
function _wmPatProgToggle(p,on){
  if(!_wmPatCurProgs)_wmPatCurProgs=_wmPatAllProgs().slice();
  if(on){if(_wmPatCurProgs.indexOf(p)<0)_wmPatCurProgs.push(p);}else{_wmPatCurProgs=_wmPatCurProgs.filter(function(x){return x!==p;});}
  if(!on&&_wmPatSelWafers){var rem=[];_wmPatSelWafers.forEach(function(pk){if(_wmPatGetProg(pk)===p)rem.push(pk);});rem.forEach(function(pk){_wmPatSelWafers.delete(pk);});}
  _wmPatBuildWaferPicker();_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
function _wmPatProgAll(){_wmPatCurProgs=null;_wmPatSelWafers=null;_wmPatBuildProgPicker();_wmPatBuildWaferPicker();_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);}
function _wmPatProgNone(){_wmPatCurProgs=[];_wmPatSelWafers=new Set();_wmPatBuildProgPicker();_wmPatBuildWaferPicker();_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);}
function _wmPatBuildLotPicker(){
  var el=document.getElementById("wm-pat-lot-picker");if(!el)return;
  var all=_wmPatAllLots();
  if(all.length<=1){el.innerHTML="";_wmPatBuildProgPicker();_wmPatBuildWaferPicker();return;}
  var h='<span style="font-size:11px;font-weight:bold;color:#d7bde2;margin-right:4px">Lots:</span>';
  h+='<span style="font-size:10px;color:#bb8fce;cursor:pointer;text-decoration:underline;margin-right:6px" onclick="_wmPatLotAll()">All</span>';
  h+='<span style="font-size:10px;color:#bb8fce;cursor:pointer;text-decoration:underline;margin-right:8px" onclick="_wmPatLotNone()">None</span>';
  all.forEach(function(lt){
    var on=!_wmPatCurLots||_wmPatCurLots.indexOf(lt)>=0;
    h+='<label style="font-size:11px;color:#fff;margin-right:6px;cursor:pointer"><input type="checkbox" data-lot="'+lt+'" '+(on?"checked ":"")+' onchange="_wmPatLotToggle(this.dataset.lot,this.checked)" style="margin-right:2px">'+lt+'</label>';
  });
  el.innerHTML=h;
  _wmPatBuildProgPicker();
  _wmPatBuildWaferPicker();
}
function _wmPatBuildWaferPicker(){
  var wp=document.getElementById("wm-pat-wafer-picker");if(!wp)return;
  var all=_wmPatAllLots();
  var activeLots=_wmPatCurLots||all;
  var activeProgs=_wmPatCurProgs;
  var multiProg=_wmPatAllProgs().length>1;
  var wKeys=Object.keys(WM_PAT.wafers).sort(function(a,b){
    var la=_wmPatGetLot(a),lb=_wmPatGetLot(b);if(la!==lb)return la<lb?-1:1;
    var wa=parseInt(_wmPatGetWfr(a))||0,wb=parseInt(_wmPatGetWfr(b))||0;if(wa!==wb)return wa-wb;
    return(_wmPatGetProg(a)<_wmPatGetProg(b)?-1:1);
  });
  var h='<span style="font-size:11px;font-weight:bold;color:#85c1e9;margin-right:4px;flex-shrink:0">Wafers:</span>';
  h+='<span style="font-size:10px;color:#85c1e9;cursor:pointer;text-decoration:underline;margin-right:4px" onclick="_wmPatWaferAll()">All</span>';
  h+='<span style="font-size:10px;color:#85c1e9;cursor:pointer;text-decoration:underline;margin-right:8px" onclick="_wmPatWaferNone()">None</span>';
  var prevLot="";
  wKeys.forEach(function(pk){
    var lt=_wmPatGetLot(pk),wn=_wmPatGetWfr(pk),pg=_wmPatGetProg(pk);
    if(activeLots.indexOf(lt)<0)return;
    if(activeProgs&&activeProgs.length&&pg&&activeProgs.indexOf(pg)<0)return;
    if(lt!==prevLot){
      if(prevLot)h+='<span style="border-left:1px solid #555;margin:0 4px;height:14px;display:inline-block"></span>';
      h+='<span style="font-size:10px;color:#aeb6bf;margin-right:2px">['+lt+']</span>';
      prevLot=lt;
    }
    var on=_wmPatSelWafers===null||_wmPatSelWafers.has(pk);
    var lbl="W"+wn+(multiProg&&pg?'<span style="font-size:9px;color:#7fb3d3;margin-left:1px">'+pg+"</span>":"");
    h+='<label style="font-size:11px;color:#d5d8dc;margin-right:4px;cursor:pointer"><input type="checkbox" data-pk="'+pk+'" '+(on?"checked ":"")+' onchange="wmPatWaferToggle(this.dataset.pk,this.checked)" style="margin-right:1px">'+lbl+'</label>';
  });
  wp.innerHTML=h;
}
function _wmPatLotToggle(lt,on){
  if(!_wmPatCurLots)_wmPatCurLots=_wmPatAllLots().slice();
  if(on){if(_wmPatCurLots.indexOf(lt)<0)_wmPatCurLots.push(lt);}else{_wmPatCurLots=_wmPatCurLots.filter(function(x){return x!==lt;});}
  if(!on&&_wmPatSelWafers){var remove=[];_wmPatSelWafers.forEach(function(pk){if(_wmPatGetLot(pk)===lt)remove.push(pk);});remove.forEach(function(pk){_wmPatSelWafers.delete(pk);});}
  _wmPatBuildWaferPicker();_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
function _wmPatLotAll(){_wmPatCurLots=null;_wmPatSelWafers=null;_wmPatBuildLotPicker();_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);}
function _wmPatLotNone(){_wmPatCurLots=[];_wmPatSelWafers=new Set();_wmPatBuildLotPicker();_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);}
function _wmPatWaferAll(){_wmPatSelWafers=null;_wmPatBuildWaferPicker();_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);}
function _wmPatWaferNone(){_wmPatSelWafers=new Set();_wmPatBuildWaferPicker();_wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);}
function _wmPatBuildCtrl(allKeys){
  _wmPatBuildRetRow(allKeys);
  _wmPatBuildShotRow(allKeys);
}
function _wmPatBuildBinRow(ibArr){
  var br=document.getElementById("wm-pat-binrow");
  if(!br||!ibArr.length){if(br)br.innerHTML="";return;}
  var h='<span style="font-size:11px;font-weight:bold;color:#5d6d7e;flex-shrink:0;margin-right:4px">IB Filter:</span>';
  ibArr.forEach(function(ibk){
    var col=_wmIbColor(ibk);
    var on=_wmPatBinChecked===null||_wmPatBinChecked.has(String(ibk));
    var _swStyle="background:"+col+";cursor:pointer;";
    h+='<label class="wm-pat-bincb"><span class="wm-pat-binsw" onclick="wmmIbHlClick('+ibk+');event.stopPropagation();" style="'+_swStyle+'"></span><input type="checkbox"'+(on?" checked":"")+' data-ib="'+ibk+'" onchange="wmPatBinToggle(+this.dataset.ib,this.checked)">IB'+ibk+'</label>';
  });
  h+='<span style="font-size:10px;color:#2471a3;cursor:pointer;text-decoration:underline;margin-left:6px" onclick="_wmPatToggleBinAll(true)">All</span>';
  h+='<span style="font-size:10px;color:#2471a3;cursor:pointer;text-decoration:underline;margin-left:4px" onclick="_wmPatToggleBinAll(false)">None</span>';
  br.innerHTML=h;
  _wmPatBuildBinRow.lastArr=ibArr;
}
function _wmPatBuildRetRow(keys){
  var rr=document.getElementById("wm-pat-retrow");
  if(!rr||!WM_PAT.hasReticle){if(rr)rr.style.display="none";return;}
  var sitesSeen={};
  keys.forEach(function(pk){
    var wdata=WM_PAT.wafers[pk];var dies=wdata&&wdata.dies?wdata.dies:wdata;if(!dies||!dies.length)return;
    var _ri=_wmRetInfoFor(pk);var rm=_ri.retMap;if(!rm)return;
    var rsl=_ri.retSiteLabels||WM_PAT.retSiteLabels||{};
    dies.forEach(function(d){
      var x=d[0],y=d[1];if(x===null||x===undefined)return;
      var info=rm[x+","+y];if(!info)return;
      var sk=info[0]+","+info[1];
      if(sitesSeen[sk]===undefined)sitesSeen[sk]=rsl[sk]!=null?rsl[sk]:null;
    });
  });
  var sks=Object.keys(sitesSeen);
  if(!sks.length){rr.style.display="none";return;}
  sks.sort(function(a,b){var la=sitesSeen[a],lb=sitesSeen[b];if(la!=null&&lb!=null)return la-lb;if(la!=null)return -1;if(lb!=null)return 1;var pa=a.split(","),pb=b.split(",");return(+pa[0]-+pb[0])||(+pa[1]-+pb[1]);});
  var _siteNum={};sks.forEach(function(sk,i){_siteNum[sk]=i+1;});
  WM_PAT._retSiteNum=_siteNum;
  var h='<span style="font-size:11px;font-weight:bold;color:#1f618d;flex-shrink:0;margin-right:4px">Die Loc:</span>';
  sks.forEach(function(sk){
    var num=_siteNum[sk];
    var on=!(_wmPatRetUnchecked&&_wmPatRetUnchecked.has(sk));
    h+='<label class="wm-pat-bincb" title="RX'+sk.split(",")[0]+' RY'+sk.split(",")[1]+'"><input type="checkbox" data-sk="'+sk+'" '+(on?"checked ":"")+' onchange="wmPatRetSiteToggle(this.dataset.sk,this.checked)">Loc '+num+'</label>';
  });
  h+='<span style="font-size:10px;color:#1f618d;cursor:pointer;text-decoration:underline;margin-left:6px" onclick="_wmPatToggleRetAll(true)">All</span>';
  h+='<span style="font-size:10px;color:#1f618d;cursor:pointer;text-decoration:underline;margin-left:4px" onclick="_wmPatToggleRetAll(false)">None</span>';
  rr.style.display="";
  rr.innerHTML=h;
}
function _wmPatToggleRetAll(on){
  if(on){_wmPatRetUnchecked=null;}else{_wmPatRetUnchecked=new Set();var rr=document.getElementById("wm-pat-retrow");if(rr)rr.querySelectorAll("input[data-sk]").forEach(function(inp){_wmPatRetUnchecked.add(inp.dataset.sk);});}
  _wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
var _wmPatShotUnchecked=null;
var _wmShotAllSis=[];
function _wmPatBuildShotRow(keys){
  var sr=document.getElementById("wm-pat-shotrow");
  if(!sr||!WM_PAT.hasReticle){if(sr)sr.style.display="none";return;}
  var shotsSeen={};
  keys.forEach(function(pk){
    var wdata=WM_PAT.wafers[pk];var dies=wdata&&wdata.dies?wdata.dies:wdata;if(!dies||!dies.length)return;
    var _ri=_wmRetInfoFor(pk);var rm=_ri.retMap;if(!rm)return;
    dies.forEach(function(d){var x=d[0],y=d[1];if(x===null||x===undefined)return;var info=rm[x+","+y];if(!info)return;shotsSeen[info[2]]=true;});
  });
  var sis=Object.keys(shotsSeen).map(Number).sort(function(a,b){return a-b;});
  if(!sis.length){sr.style.display="none";return;}
  _wmShotAllSis=sis;
  var nSel=sis.filter(function(si){return !(_wmPatShotUnchecked&&_wmPatShotUnchecked.has(si));}).length;
  var btnLbl=nSel===sis.length?"All ("+sis.length+")":nSel+" / "+sis.length+" selected";
  var h='<span style="font-size:11px;font-weight:bold;color:#6c3483;flex-shrink:0;margin-right:4px">Shot #:</span>';
  h+='<div style="position:relative;display:inline-block;vertical-align:middle">';
  h+='<button id="wm-shot-dd-btn" onclick="_wmShotDdOpen()" style="font-size:11px;padding:1px 8px 1px 6px;border:1px solid #c39bd3;border-radius:3px;background:#f5eef8;color:#6c3483;cursor:pointer;min-width:120px;text-align:left;white-space:nowrap">'+btnLbl+' &#9660;</button>';
  h+='<div id="wm-shot-dd" style="display:none;position:absolute;z-index:9999;background:#fff;border:1px solid #c39bd3;border-radius:4px;box-shadow:0 4px 12px rgba(0,0,0,.18);padding:0;min-width:160px;top:calc(100% + 2px);left:0">';
  h+='<div style="padding:4px 6px;border-bottom:1px solid #eee"><input id="wm-shot-dd-search" type="text" placeholder="Search shots..." oninput="_wmShotDdFilter(this.value)" style="width:100%;font-size:11px;border:1px solid #ddd;border-radius:2px;padding:2px 4px;box-sizing:border-box"/></div>';
  h+='<div style="display:flex;gap:6px;padding:2px 6px;border-bottom:1px solid #eee;font-size:10px">';
  h+='<a href="#" onclick="_wmPatToggleShotAll(true);return false" style="color:#6c3483;font-weight:bold">All</a>';
  h+='<a href="#" onclick="_wmPatToggleShotAll(false);return false" style="color:#c0392b;font-weight:bold">None</a></div>';
  h+='<div id="wm-shot-dd-list" style="max-height:180px;overflow-y:auto;padding:2px 0">';
  sis.forEach(function(si){
    var on=!(_wmPatShotUnchecked&&_wmPatShotUnchecked.has(si));
    h+='<label data-shot="'+si+'" style="display:flex;align-items:center;gap:5px;padding:2px 8px;font-size:11px;cursor:pointer;white-space:nowrap"><input type="checkbox" data-si="'+si+'" '+(on?"checked ":"")+' onchange="_wmPatShotToggle(+this.dataset.si,this.checked)">Shot '+si+'</label>';
  });
  h+='</div></div></div>';
  h+='<span style="color:#ccc;margin:0 8px">|</span>';
  h+='<span style="font-size:11px;font-weight:bold;color:#6d4c41;flex-shrink:0;margin-right:4px">Excl. edge rows:</span>';
  var _edgeOpts=[0,1,2,3,4,5,6,7,8,9,10];
  h+='<select class="wm-edge-sel" onchange="_wmSetEdgeRows(+this.value)" style="font-size:11px;padding:1px 4px;background:#f5f5f5;color:#6d4c41;border:1px solid #bcaaa4;border-radius:3px;cursor:pointer">';
  _edgeOpts.forEach(function(n){h+='<option value="'+n+'" '+(_wmEdgeExcRows===n?"selected":"")+'>'+n+'</option>';});
  h+='</select>';
  h+='<span style="color:#ccc;margin:0 8px">|</span>';
  h+='<span style="font-size:11px;font-weight:bold;color:#1f618d;flex-shrink:0;margin-right:4px">&#8805;IB:</span>';
  [1,2,3,4,5].forEach(function(v){h+='<label style="display:flex;align-items:center;gap:2px;font-size:11px;color:#1f618d;cursor:pointer;white-space:nowrap;margin-right:4px"><input type="radio" name="wm-thr-rb" value="'+v+'" '+(_wmFailThr===v?"checked ":"")+' onchange="_wmSetFailThr(+this.value)" style="cursor:pointer">'+v+'</label>';});
  sr.style.display="";
  sr.innerHTML=h;
}
function _wmShotDdOpen(){
  var dd=document.getElementById("wm-shot-dd");if(!dd)return;
  var isOpen=dd.style.display!=="none";
  if(!isOpen){
    dd.style.display="";
    var inp=document.getElementById("wm-shot-dd-search");if(inp){inp.value="";_wmShotDdFilter("");inp.focus();}
    setTimeout(function(){document.addEventListener("click",function _cl(e){var btn=document.getElementById("wm-shot-dd-btn");if(!dd.contains(e.target)&&e.target!==btn){dd.style.display="none";document.removeEventListener("click",_cl,true);}},true);},0);
  } else { dd.style.display="none"; }
}
function _wmShotDdFilter(q){
  var list=document.getElementById("wm-shot-dd-list");if(!list)return;
  var s=q.trim().toLowerCase();
  list.querySelectorAll("label[data-shot]").forEach(function(lbl){
    var val="shot "+lbl.dataset.shot;
    lbl.style.display=(s===""||val.indexOf(s)>=0)?"":"none";
  });
}
function _wmShotDdRefreshBtn(){
  var btn=document.getElementById("wm-shot-dd-btn");if(!btn)return;
  var sis=_wmShotAllSis;
  var nSel=sis.filter(function(si){return !(_wmPatShotUnchecked&&_wmPatShotUnchecked.has(si));}).length;
  btn.innerHTML=(nSel===sis.length?"All ("+sis.length+")":nSel+" / "+sis.length+" selected")+" &#9660;";
}
function _wmPatShotToggle(si,on){
  if(_wmPatShotUnchecked===null)_wmPatShotUnchecked=new Set();
  if(on){_wmPatShotUnchecked.delete(si);}else{_wmPatShotUnchecked.add(si);}
  if(_wmPatShotUnchecked.size===0)_wmPatShotUnchecked=null;
  var ddInp=document.querySelector('#wm-shot-dd-list input[data-si="'+si+'"]');if(ddInp)ddInp.checked=on;
  _wmShotDdRefreshBtn();
  _wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
function _wmPatToggleShotAll(on){
  if(on){
    _wmPatShotUnchecked=null;
    var list=document.getElementById("wm-shot-dd-list");
    if(list)list.querySelectorAll("input[data-si]").forEach(function(inp){inp.checked=true;});
  } else {
    _wmPatShotUnchecked=new Set(_wmShotAllSis);
    var list=document.getElementById("wm-shot-dd-list");
    if(list)list.querySelectorAll("input[data-si]").forEach(function(inp){inp.checked=false;});
  }
  _wmShotDdRefreshBtn();
  _wmPatRender();wmPatRenderReticle();if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
function _wmPatRender(){
  var maps=document.getElementById("wm-pat-maps");
  var tbody=document.getElementById("wm-pat-tbody");
  var impactBody=document.getElementById("wm-pat-impact-body");
  if(!maps||!tbody)return;
  var allKeys=Object.keys(WM_PAT.wafers).filter(function(k){return _wmPatMatchLots(k)&&_wmPatMatchProgs(k);}).sort(function(a,b){
    var la=_wmPatGetLot(a),lb=_wmPatGetLot(b);if(la!==lb)return la<lb?-1:1;
    var wa=parseInt(_wmPatGetWfr(a))||0,wb=parseInt(_wmPatGetWfr(b))||0;if(wa!==wb)return wa-wb;
    return(_wmPatGetProg(a)<_wmPatGetProg(b)?-1:1);
  });
  _wmPatBuildCtrl(allKeys);
  var keys=_wmPatSelWafers===null?allKeys:allKeys.filter(function(k){return _wmPatSelWafers.has(k);});
  var FIXED_W=_wmTileW,pad=2;
  var mapsHtml="",tbHtml="",ibSeen={},ibPatAcc={},sc_acc={};
  var _bar=function(v){var pw=Math.round(v*90);var c=v<0.35?"#27ae60":v<0.65?"#e67e22":"#c0392b";return'<span class="wm-bar-bg"><span class="wm-bar-fg" style="width:'+pw+'px;background:'+c+'"></span></span><span style="font-size:10px;color:#555;margin-left:3px">'+Math.round(v*100)+'%</span>';};
  keys.forEach(function(pk){
    var wdata=WM_PAT.wafers[pk];
    var dies=wdata&&wdata.dies?wdata.dies:wdata;
    var mLot=wdata.lot||pk.split("::")[0];
    var mWfr=wdata.wafer||pk.split("::")[1];
    var mProg=wdata.program||_wmPatGetProg(pk);
    var mMat=wdata.material||"";
    var multiProg2=_wmPatAllProgs().length>1;
    var mProgLbl=multiProg2&&mProg?' <span style="font-size:9px;color:#7fb3d3">'+mProg+"</span>":"";
    if(!dies||!dies.length){
      mapsHtml+='<div style="text-align:center"><div class="wm-wlbl" style="color:#aaa">'+mLot+' W'+mWfr+mProgLbl+'</div><div style="font-size:10px;color:#ccc;margin-top:4px">no data</div></div>';
      tbHtml+='<tr><td style="font-size:10px">'+mLot+'</td><td style="font-weight:bold">W'+mWfr+mProgLbl+'</td><td colspan="9" style="color:#bbb;font-size:10px">no data</td></tr>';
      return;
    }
    var xs=[],ys=[];
    dies.forEach(function(d){if(d[0]!==null){xs.push(d[0]);ys.push(d[1]);}});
    var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
    var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
    var cs=Math.max(2,(FIXED_W-pad*2)/(xMax-xMin+1));
    var xSpan=xMax-xMin,ySpan=yMax-yMin;
    var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
    var W=FIXED_W,H=Math.round((yMax-yMin+1)*csy+pad*2);
    var xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;
    var xRad=(xMax-xMin)/2||1,yRad=(yMax-yMin)/2||1;
    var ibCoords={},failXn=[],failYn=[],failActX=[],failActY=[],totalDies=0,failDies=0;
    var failShotIdx=new Set();
    var _pkSiteFailCnt={};
    var rects=[];
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2];if(x===null)return;
      totalDies++;
      var px=(pad+(x-xMin)*cs).toFixed(1),py=(pad+(yMax-y)*csy).toFixed(1);
      var ibKey=(ib!==null&&ib!==undefined)?ib:null;
      var fill=_wmIbColor(ibKey);ibSeen[String(ibKey)]=fill;
      var binOn=(_wmPatBinChecked===null||_wmPatBinChecked.has(String(ibKey)));
      var opacity=binOn?"1":"0.08";
      if(binOn&&_wmPatRetUnchecked&&_wmPatRetUnchecked.size>0&&WM_PAT.hasReticle){var _dri=_wmRetInfoFor(pk);var _dri2=_dri.retMap&&_dri.retMap[x+","+y];if(_dri2&&_wmPatRetUnchecked.has(_dri2[0]+","+_dri2[1]))opacity="0.12";}
      if(binOn&&_wmPatShotUnchecked&&_wmPatShotUnchecked.size>0&&WM_PAT.hasReticle){var _sri=_wmRetInfoFor(pk);var _sri2=_sri.retMap&&_sri.retMap[x+","+y];if(_sri2&&_wmPatShotUnchecked.has(_sri2[2]))opacity="0.08";}
      if(_wmIsFail(ibKey)&&ibKey!==null&&binOn){
        var xn=(x-xCtr)/xRad,yn=(y-yCtr)/yRad;
        var _isEdge=(_wmEdgeExcRows>0&&(x<xMin+_wmEdgeExcRows||x>xMax-_wmEdgeExcRows||y<yMin+_wmEdgeExcRows||y>yMax-_wmEdgeExcRows));
        if(_isEdge){opacity="0.15";}else{
          failXn.push(xn);failYn.push(yn);failActX.push(x);failActY.push(y);failDies++;
          if(WM_PAT.hasReticle){var _wri=_wmRetInfoFor(pk);var _ri=_wri.retMap&&_wri.retMap[x+","+y];if(_ri){failShotIdx.add(_ri[2]);var _sk0=_ri[0]+","+_ri[1];_pkSiteFailCnt[_sk0]=(_pkSiteFailCnt[_sk0]||0)+1;}}
          if(!ibCoords[ibKey])ibCoords[ibKey]={xn:[],yn:[],ax:[],ay:[]};
          ibCoords[ibKey].xn.push(xn);ibCoords[ibKey].yn.push(yn);
          ibCoords[ibKey].ax.push(x);ibCoords[ibKey].ay.push(y);
        }
      }
      rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'" height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" opacity="'+opacity+'"/>');
      var _dieTag2="";
      if(WM_PAT.hasReticle&&WM_PAT._retSiteNum){var _dtMap=_wmRetInfoFor(pk).retMap;var _dtInf=_dtMap&&_dtMap[x+","+y];if(_dtInf){var _dtSk=_dtInf[0]+","+_dtInf[1];_dieTag2=String(WM_PAT._retSiteNum[_dtSk]||"");}};
      var _tagFs2=Math.max(3,Math.min(6,Math.round(cs*0.35)));
      if(_dieTag2&&cs>=4){rects.push('<text x="'+(parseFloat(px)+cs-0.5).toFixed(1)+'" y="'+(parseFloat(py)+_tagFs2+0.5).toFixed(1)+'" text-anchor="end" font-size="'+_tagFs2+'" fill="#000" font-weight="bold" opacity="'+opacity+'" pointer-events="none">'+_dieTag2+'</text>');}
    });
    var _pkRetInfo=_wmRetInfoFor(pk);
    var _pkShots=(_pkRetInfo.retShots&&_pkRetInfo.retShots.length)?_pkRetInfo.retShots:WM_PAT.retShots||[];
    var _topLocStr="\u2014";
    if(WM_PAT.hasReticle&&WM_PAT._retSiteNum){var _skeys=Object.keys(_pkSiteFailCnt);if(_skeys.length){_skeys.sort(function(a,b){return _pkSiteFailCnt[b]-_pkSiteFailCnt[a];});var _topSk=_skeys[0];var _topN=WM_PAT._retSiteNum[_topSk];if(_topN!=null){var _topPct=failDies>0?Math.round(_pkSiteFailCnt[_topSk]/failDies*100):0;_topLocStr="Loc"+_topN+" ("+_topPct+"%)";}else _topLocStr=_topSk;}}
    var retOut="";
    _pkShots.forEach(function(s,si){
      var sx=(pad+(s[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-s[3])*csy).toFixed(1);
      var sw=((s[2]-s[0]+1)*cs).toFixed(1),sh=((s[3]-s[1]+1)*csy).toFixed(1);
      retOut+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#2471a3" stroke-width="0.7" opacity="0.35"/>';
      if(cs>=6){var tx=(+sx+(+sw)/2).toFixed(1),ty=(+sy+8).toFixed(1);retOut+='<text x="'+tx+'" y="'+ty+'" text-anchor="middle" font-size="7" fill="#2471a3" opacity="0.7" pointer-events="none">'+si+'</text>';}
    });
    _pkShots.forEach(function(s,si){
      if(!failShotIdx.has(si))return;
      var sx=(pad+(s[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-s[3])*csy).toFixed(1);
      var sw=((s[2]-s[0]+1)*cs).toFixed(1),sh=((s[3]-s[1]+1)*csy).toFixed(1);
      retOut+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#c0392b" stroke-width="1.5" opacity="0.9"/>';
    });
    var failPct=totalDies>0?(failDies/totalDies*100).toFixed(1)+"%":"0%";
    var driverIb="\u2014";
    if(failDies>0){
      var drKeys=Object.keys(ibCoords).sort(function(a,b){return ibCoords[b].xn.length-ibCoords[a].xn.length;});
      var topN=drKeys.length?ibCoords[drKeys[0]].xn.length:0;
      driverIb=drKeys.filter(function(k){return ibCoords[k].xn.length>=topN*0.8;}).map(function(k){return"IB"+k+"(n="+ibCoords[k].xn.length+")";}).join(", ");
    }
    var clipId="wmpc_"+pk.replace(/[^a-z0-9]/gi,"_");
    var cx=(pad+(xCtr-xMin)*cs+cs*0.45).toFixed(1);
    var cy=(pad+(yMax-yCtr)*csy+csy*0.45).toFixed(1);
    var rx=(xRad*cs+cs*0.5).toFixed(1);
    var ry=(yRad*csy+csy*0.5).toFixed(1);
    var sc={center:0,edge:0,donut:0,systematic:0,reticle:0,random:0};
    var retSc=0;
    var _psc={confidence:"LOW"};
    if(failDies>=3){
      _psc=_wmScorePattern(failXn,failYn);
      sc.center=_psc.center;sc.edge=_psc.edge;sc.donut=_psc.donut;sc.systematic=_psc.systematic;
      if(WM_PAT.hasReticle&&failActX.length>0){
        var _wri2=_wmRetInfoFor(pk);
        retSc=_wmScoreReticle(failActX,failActY,_wri2.retMap||WM_PAT.retMap,_wri2.retSiteTotals||WM_PAT.retSiteTotals);
        sc.reticle=retSc;
      }
      var dominated=Math.max(sc.center,sc.edge,sc.donut,sc.systematic,sc.reticle);
      sc.random=Math.max(0,1-dominated);
    } else {
      _psc={confidence:"LOW"};
      sc.random=failDies>0?1:0;
    }
    sc_acc[pk]={center:sc.center,edge:sc.edge,donut:sc.donut,systematic:sc.systematic,reticle:sc.reticle,random:sc.random,topLoc:_topLocStr,siteFailCnt:_pkSiteFailCnt,failDies:failDies};
    var dims2=["center","edge","donut","systematic","reticle","random"];
    var primary="RANDOM",pCol=_pColors.RANDOM;
    var bestScore=sc.random;
    dims2.forEach(function(d){if(d!=="random"&&(sc[d]||0)>bestScore){bestScore=sc[d];primary=d.toUpperCase();pCol=_pColors[d.toUpperCase()]||"#555";}});
    var _confCol={HIGH:"#27ae60",MEDIUM:"#e67e22",LOW:"#e74c3c"}[_psc.confidence]||"#999";
    if(failDies>0){
      Object.keys(ibCoords).forEach(function(ibk){
        if(!ibPatAcc[ibk])ibPatAcc[ibk]={cnt:0,dies:0,center:0,edge:0,donut:0,systematic:0,reticle:0,random:0};
        var acc=ibPatAcc[ibk];acc.cnt++;acc.dies+=ibCoords[ibk].xn.length;
        acc.center+=sc.center;acc.edge+=sc.edge;acc.donut+=sc.donut;
        acc.systematic+=sc.systematic;acc.reticle+=sc.reticle;acc.random+=sc.random;
      });
    }
    var svgStr='<svg width="'+W+'" height="'+H+'" style="display:block;margin:0 auto">'
      +'<defs><clipPath id="'+clipId+'"><ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry+'"/></clipPath></defs>'
      +'<g clip-path="url(#'+clipId+')">'+rects.join("")+retOut+'</g>'
      +'<ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry+'" fill="none" stroke="#bdc3c7" stroke-width="1.5"/></svg>';
    mapsHtml+='<div style="text-align:center">'
      +svgStr
      +'<div style="font-size:10px;color:'+pCol+';font-weight:bold;margin-top:2px">'+primary+'</div>'
      +'<div class="wm-wlbl" style="margin-top:2px">'+mLot+' W'+mWfr+mProgLbl+'</div>'
      +'</div>';
    tbHtml+='<tr>'
      +'<td style="font-size:10px;white-space:nowrap">'+mLot+'</td>'
      +'<td style="font-weight:bold;white-space:nowrap">W'+mWfr+mProgLbl+'</td>'
      +'<td style="font-size:10px;color:#555;white-space:nowrap">'+mMat+'</td>'
      +'<td style="font-weight:bold;color:'+pCol+'">'+primary+'</td>'
      +'<td style="white-space:nowrap;color:'+_confCol+';font-size:10px">'+_psc.confidence+'</td>'
      +'<td style="white-space:nowrap">'+failPct+'<span style="font-size:9px;color:#999;margin-left:2px">(n='+failDies+')</span></td>'
      +'<td style="font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+driverIb+'">'+driverIb+'</td>'
      +'<td>'+_bar(sc.center||0)+'</td>'
      +'<td>'+_bar(sc.edge||0)+'</td>'
      +'<td>'+_bar(sc.donut||0)+'</td>'
      +'<td>'+_bar(sc.systematic||0)+'</td>'
      +(WM_PAT.hasReticle?'<td>'+_bar(retSc||0)+'</td>':'')
      +(WM_PAT.hasReticle?'<td style="font-size:10px;white-space:nowrap;color:#1f618d">'+(_topLocStr||'\u2014')+'</td>':'')
      +'<td>'+_bar(sc.random||0)+'</td></tr>';
  });
  maps.innerHTML=mapsHtml||'<span style="color:#999;font-size:12px">No wafers selected</span>';
  tbody.innerHTML=tbHtml;
  var ltEl=document.getElementById("wm-pat-lot-trend");
  if(ltEl){
    var _lta={};
    keys.forEach(function(pk){
      var w=WM_PAT.wafers[pk];var lot=(w&&w.lot)||pk.split("::")[0];
      if(!_lta[lot])_lta[lot]={n:0,center:0,edge:0,donut:0,systematic:0,reticle:0,random:0,siteFailAgg:{},failDiesAgg:0};
      var _s=_lta[lot];_s.n++;
      if(sc_acc[pk]){_s.center+=sc_acc[pk].center;_s.edge+=sc_acc[pk].edge;_s.donut+=sc_acc[pk].donut;_s.systematic+=sc_acc[pk].systematic;_s.reticle+=sc_acc[pk].reticle;_s.random+=sc_acc[pk].random;_s.failDiesAgg+=sc_acc[pk].failDies||0;var _pSfc=sc_acc[pk].siteFailCnt||{};Object.keys(_pSfc).forEach(function(sk){_s.siteFailAgg[sk]=(_s.siteFailAgg[sk]||0)+_pSfc[sk];});}
    });
    var _lots=Object.keys(_lta).sort();
    if(_lots.length){
      var _lth='<table style="width:100%;border-collapse:collapse;font-size:11px"><thead><tr style="background:#d6eaf8"><th style="text-align:left;padding:2px 6px;color:#1a5276">Lot</th><th style="padding:2px 4px;color:#555">Wfrs</th><th style="padding:2px 6px;color:#555">Primary</th><th style="padding:2px 4px;color:#c0392b">Center</th><th style="padding:2px 4px;color:#e67e22">Edge</th><th style="padding:2px 4px;color:#8e44ad">Donut</th><th style="padding:2px 4px;color:#2471a3">Systematic</th>'+(WM_PAT.hasReticle?'<th style="padding:2px 4px;color:#1f618d">Reticle</th>':'')+(WM_PAT.hasReticle?'<th style="padding:2px 4px;color:#1a5276">Top Die Loc</th>':'')+'<th style="padding:2px 4px;color:#27ae60">Random</th></tr></thead><tbody>';
      _lots.forEach(function(lot,li){
        var a=_lta[lot],n=a.n||1;
        var lsc={center:a.center/n,edge:a.edge/n,donut:a.donut/n,systematic:a.systematic/n,reticle:a.reticle/n,random:a.random/n};
        var lPrim='RANDOM',lCol=_pColors.RANDOM,lV=lsc.random;
        ['center','edge','donut','systematic','reticle'].forEach(function(d){if((lsc[d]||0)>lV){lV=lsc[d];lPrim=d.toUpperCase();lCol=_pColors[d.toUpperCase()]||'#555';}});
        var bg=li%2?'background:#f7f9fc':'';
        _lth+='<tr style="'+bg+'"><td style="padding:2px 6px;font-weight:bold;color:#1a5276">'+lot+'</td>'
          +'<td style="text-align:center;padding:2px 4px;color:#555">'+a.n+'</td>'
          +'<td style="font-weight:bold;color:'+lCol+';padding:2px 6px">'+lPrim+'</td>'
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.center*100)+'%</td>'
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.edge*100)+'%</td>'
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.donut*100)+'%</td>'
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.systematic*100)+'%</td>'
          +(WM_PAT.hasReticle?'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.reticle*100)+'%</td>':'')
          +(WM_PAT.hasReticle?(function(){var _ltSkeys=Object.keys(a.siteFailAgg);if(!_ltSkeys.length)return'<td style="text-align:center;padding:2px 4px;color:#1a5276;font-size:10px">\u2014</td>';_ltSkeys.sort(function(x,y){return a.siteFailAgg[y]-a.siteFailAgg[x];});var _ltTopSk=_ltSkeys[0];var _ltTopN=WM_PAT._retSiteNum&&WM_PAT._retSiteNum[_ltTopSk];var _ltPct=a.failDiesAgg>0?Math.round(a.siteFailAgg[_ltTopSk]/a.failDiesAgg*100):0;var _ltLbl=_ltTopN!=null?"Loc"+_ltTopN+" ("+_ltPct+"%)":_ltTopSk;return'<td style="text-align:center;padding:2px 4px;color:#1a5276;font-size:10px">'+_ltLbl+'</td>';}()):'')
          +'<td style="text-align:center;padding:2px 4px">'+Math.round(lsc.random*100)+'%</td></tr>';
      });
      _lth+='</tbody></table>';
      ltEl.innerHTML=_lth;
    }
  }
  var ibKeys=Object.keys(ibPatAcc).sort(function(a,b){return+a-+b;});
  if(impactBody&&ibKeys.length){
    var dims=["center","edge","donut","systematic","random"];
    if(WM_PAT.hasReticle)dims.splice(4,0,"reticle");
    var dimLbls={center:"Center",edge:"Edge",donut:"Donut",systematic:"Syst.",reticle:"Reticle",random:"Rnd"};
    var ibh='<div style="font-size:10px;color:#888;margin-bottom:6px">Avg pattern score per fail IB (across displayed wafers).</div>';
    ibKeys.forEach(function(ibk){
      var a=ibPatAcc[ibk],cnt=a.cnt||1,nDies=a.dies||0;
      var col=_wmIbColor(+ibk);
      var bestDim="random",bestVal=a.random/cnt;
      dims.forEach(function(d){if(a[d]/cnt>bestVal){bestVal=a[d]/cnt;bestDim=d;}});
      var bdCol=_pColors[bestDim.toUpperCase()]||"#555";
      ibh+='<div class="wm-impact-row" style="margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid #eee">'
        +'<div class="wm-impact-lbl" style="color:'+col+'">IB'+ibk+'<span style="font-size:9px;color:#999;margin-left:3px">(n='+nDies+')</span></div>'
        +'<div style="flex:1;display:flex;flex-wrap:wrap;gap:3px 8px">';
      dims.forEach(function(d){
        var v=a[d]/cnt,bc=v<0.35?"#27ae60":v<0.65?"#e67e22":"#c0392b";
        ibh+='<div style="display:inline-flex;align-items:center;gap:2px;font-size:10px">'
          +'<span style="width:32px;color:#666">'+dimLbls[d]+'</span>'
          +'<div class="wm-impact-bar" style="width:44px"><div class="wm-impact-fill" style="width:'+Math.round(v*44)+'px;background:'+bc+'"></div></div>'
          +'<span style="width:28px;font-size:10px;color:#555">'+Math.round(v*100)+'%</span></div>';
      });
      ibh+='</div><div style="font-size:10px;font-weight:bold;color:'+bdCol+';white-space:nowrap;margin-left:4px">\u2192'+bestDim.toUpperCase()+'</div></div>';
    });
    impactBody.innerHTML=ibh;
  }else if(impactBody){impactBody.innerHTML='<span style="color:#aaa;font-size:11px">No fail die data</span>';}
  var allIbArr=[];
  Object.keys(ibSeen).forEach(function(k){if(k!=="null"&&k!=="undefined")allIbArr.push(+k);});
  allIbArr.sort(function(a,b){return a-b;});
  _wmPatBuildBinRow(allIbArr);
  _wmPatBuildRetRow(keys);
  _wmBuildModeMap(keys);
}
function wmPatLTab(t){
  document.querySelectorAll(".wm-pat-ltab").forEach(function(b){b.classList.toggle("on",b.dataset.ltab===t);});
  document.querySelectorAll(".wm-pat-lpane").forEach(function(p){p.classList.toggle("on",p.id==="wm-pat-lpane-"+t);});
  if(t==="composite"){
    var _mk=_wmPatBuildBinRow.lastMapKeys;
    if(!_mk){_mk=Object.keys(WM_PAT.wafers).filter(function(k){return _wmPatMatchLots(k)&&_wmPatMatchProgs(k);});}
    _wmBuildModeMap(_mk);
  }
}
function wmmIbHlClick(ibk){
  _wmmHlIb=(_wmmHlIb!==null&&String(_wmmHlIb)===String(ibk))?null:String(ibk);
  if(_wmPatBuildBinRow.lastArr)_wmPatBuildBinRow(_wmPatBuildBinRow.lastArr);
  if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
function _wmmHeatColor(t){
  if(t<=0)return"#f0f0f0";
  t=Math.min(1,t);
  var hue=Math.round(30*(1-t));
  var sat=t<0.05?Math.round(t/0.05*100):100;
  var lit=Math.round(95-65*t);
  return"hsl("+hue+","+sat+"%,"+lit+"%)";
}
function _wmmToggleHeat(){
  _wmmHeatMode=!_wmmHeatMode;
  if(_wmPatBuildBinRow.lastMapKeys)_wmBuildModeMap(_wmPatBuildBinRow.lastMapKeys);
}
function _wmBuildModeMap(keys){
  var _retShots=WM_PAT.retShots||[];
  var _retMap=WM_PAT.retMap||{};
  if(keys&&keys.length){var _ri0=_wmRetInfoFor(keys[0]);if(_ri0){_retShots=_ri0.retShots||_retShots;_retMap=_ri0.retMap||_retMap;}}
  _wmPatBuildBinRow.lastMapKeys=keys;
  var el=document.getElementById("wm-pat-modemap-body");if(!el)return;
  var el2=document.getElementById("wm-pat-modemap-body2");
  if(!keys||!keys.length){el.innerHTML='<span style="color:#999;font-size:11px">No wafers selected</span>';if(el2)el2.innerHTML=el.innerHTML;return;}
  var pos={};
  keys.forEach(function(pk){
    var wdata=WM_PAT.wafers[pk];var dies=wdata&&wdata.dies?wdata.dies:wdata;if(!dies||!dies.length)return;
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2];if(x===null||x===undefined)return;
      var key=x+","+y;
      if(!pos[key])pos[key]={x:x,y:y,cnt:{}};
      var ibk=String(ib===null||ib===undefined?"null":ib);
      pos[key].cnt[ibk]=(pos[key].cnt[ibk]||0)+1;
    });
  });
  var entries=Object.values(pos);
  if(!entries.length){el.innerHTML='<span style="color:#999;font-size:11px">No die data</span>';return;}
  var xs=entries.map(function(e){return e.x;}),ys=entries.map(function(e){return e.y;});
  var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
  var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
  var maxW=580,pad=8;
  var cs=Math.max(5,Math.floor((maxW-pad*2)/(xMax-xMin+1)));
  var xSpan=xMax-xMin,ySpan=yMax-yMin;
  var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
  var W=Math.round((xMax-xMin+1)*cs+pad*2),H=Math.round((ySpan+1)*csy+pad*2);
  var cx=pad+xSpan/2*cs+cs/2,cy=pad+ySpan/2*csy+csy/2;
  var rx=xSpan/2*cs+cs/2,ry=ySpan/2*csy+csy/2;
  var clipId="wmm-clip-"+Date.now();
  var _rsl=WM_PAT.retSiteLabels||{};if(keys&&keys.length){var _ri0b=_wmRetInfoFor(keys[0]);if(_ri0b&&_ri0b.retSiteLabels)_rsl=_ri0b.retSiteLabels;}
  var _snumM=WM_PAT._retSiteNum||{};var dieNumMap={};if(_retMap){Object.keys(_retMap).forEach(function(k){var v=_retMap[k];var sk=v[0]+","+v[1];var lbl=(_snumM[sk]!=null)?_snumM[sk]:_rsl[sk];if(lbl!=null)dieNumMap[k]=lbl;});}
  var fsize2=Math.max(5,Math.min(9,Math.round(cs*0.55)));
  var rects=[],legSeen={};
  var _failCounts={},_maxFailCount=1;
  if(_wmmHeatMode){
    entries.forEach(function(e){
      var fc=0;Object.keys(e.cnt).forEach(function(ibk){if(ibk!=="null"&&(_wmPatBinChecked===null||_wmPatBinChecked.has(ibk)))fc+=e.cnt[ibk];});
      _failCounts[e.x+","+e.y]=fc;if(fc>_maxFailCount)_maxFailCount=fc;
    });
  }
  entries.forEach(function(e){
    var modeIb="null",modeCount=0;
    var _sortedIbks=Object.keys(e.cnt).sort(function(a,b){return e.cnt[b]-e.cnt[a];});
    for(var _ki=0;_ki<_sortedIbks.length;_ki++){var _ibk=_sortedIbks[_ki];if(_wmPatBinChecked===null||_wmPatBinChecked.has(_ibk)){modeIb=_ibk;modeCount=e.cnt[_ibk];break;}}
    var ibVal=modeIb==="null"?null:(isNaN(+modeIb)?null:+modeIb);
    var fill=modeCount>0?_wmIbColor(ibVal):"white";
    var dispFill;
    if(_wmmHeatMode){var _fc=_failCounts[e.x+","+e.y]||0;dispFill=_wmmHeatColor(_fc/_maxFailCount);}
    else if(_wmmHlIb!==null){var _hlC=e.cnt[_wmmHlIb]||0;var _hlF=_wmIbColor(+_wmmHlIb);dispFill=_hlC>0?_hlF:"white";}
    else{dispFill=fill;}
    if(_wmPatRetUnchecked&&_wmPatRetUnchecked.size>0&&_retMap){var _cri=_retMap[e.x+","+e.y];if(_cri&&_wmPatRetUnchecked.has(_cri[0]+","+_cri[1]))dispFill="white";}
    if(_wmPatShotUnchecked&&_wmPatShotUnchecked.size>0&&_retMap){var _csi=_retMap[e.x+","+e.y];if(_csi&&_wmPatShotUnchecked.has(_csi[2]))dispFill="white";}
    if(_wmEdgeExcRows>0&&(e.x<xMin+_wmEdgeExcRows||e.x>xMax-_wmEdgeExcRows||e.y<yMin+_wmEdgeExcRows||e.y>yMax-_wmEdgeExcRows))dispFill="rgba(220,220,220,0.3)";
    var px=(pad+(e.x-xMin)*cs).toFixed(1),py=(pad+(yMax-e.y)*csy).toFixed(1);
    var _legIb="null",_legCnt=0;Object.keys(e.cnt).forEach(function(ibk){if(e.cnt[ibk]>_legCnt){_legCnt=e.cnt[ibk];_legIb=ibk;}});
    var _legVal=_legIb==="null"?null:(isNaN(+_legIb)?null:+_legIb);
    legSeen[_legIb]={fill:_wmIbColor(_legVal),label:_legVal===null?"N/A":"IB"+_legIb};
    rects.push('<rect data-ib="'+modeIb+'" data-fill="'+dispFill+'" x="'+px+'" y="'+py+'" width="'+cs.toFixed(1)+'" height="'+csy.toFixed(1)+'" fill="'+dispFill+'" stroke="#fff" stroke-width="0.3" class="wmm-die"/>');
    var _dnum=dieNumMap[e.x+","+e.y]||"";
    if(_dnum){
      var _tc=(function(c){if(!c||c==="white")return"#444";var h=c.replace("#","");if(h.length===3)h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];var r=parseInt(h.substr(0,2),16),g=parseInt(h.substr(2,2),16),b=parseInt(h.substr(4,2),16);return(0.299*r+0.587*g+0.114*b)<128?"#fff":"#222";})(dispFill);
      rects.push('<text x="'+(parseFloat(px)+cs/2).toFixed(1)+'" y="'+(parseFloat(py)+csy*0.62).toFixed(1)+'" text-anchor="middle" font-size="'+fsize2+'" fill="'+_tc+'" pointer-events="none">'+_dnum+'</text>');
    }
  });
  var shotRects="";
  if(_retShots&&_retShots.length){
    _retShots.forEach(function(s,si){
      var sx=(pad+(s[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-s[3])*csy).toFixed(1);
      var sw=((s[2]-s[0]+1)*cs).toFixed(1),sh=((s[3]-s[1]+1)*csy).toFixed(1);
      shotRects+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#1a6bb0" stroke-width="1.5" opacity="0.85"/>';
      if(cs>=6){var tx=(parseFloat(sx)+parseFloat(sw)/2).toFixed(1),ty=(parseFloat(sy)+9).toFixed(1);shotRects+='<text x="'+tx+'" y="'+ty+'" text-anchor="middle" font-size="8" fill="#1a6bb0" opacity="0.85" pointer-events="none">'+si+'</text>';}
    });
  }
  var svgStr='<svg id="wmm-svg" viewBox="0 0 '+W+' '+H+'" width="100%" xmlns="http://www.w3.org/2000/svg" style="display:block;height:auto">'
    +'<title>Composite wafer map</title>'
    +'<defs><clipPath id="'+clipId+'"><ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry+'"/></clipPath></defs>'
    +'<g clip-path="url(#'+clipId+')">'+rects.join("")+'</g>'
    +'<ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry+'" fill="none" stroke="#bdc3c7" stroke-width="1.5"/>'
    +shotRects+'</svg>';
  var n=keys.length;
  var _heatLeg=_wmmHeatMode?'<div style="display:flex;align-items:center;gap:4px;font-size:10px;color:#666;flex-shrink:0"><span>Low</span><div style="width:80px;height:8px;border-radius:3px;background:linear-gradient(to right,#f0f0f0,hsl(30,100%,62%),hsl(0,100%,30%))"></div><span>High bin density</span></div>':'';
  var _btnStyle=_wmmHeatMode?"background:#c0392b;color:#fff;border:1px solid #c0392b":"background:#f8f9fa;color:#1a5276;border:1px solid #bdc3c7";
  el.innerHTML='<div style="display:flex;flex-direction:column;align-items:center;gap:6px;width:100%">'
    +'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:center">'
    +'<p style="font-size:10px;color:#666;margin:0">'+(_wmmHeatMode?"Bin Density":"Mode IB")+' \u00b7 '+n+' wafer'+(n!==1?'s':'')+' \u2014 '+(_wmmHeatMode?"darker = more bins across wafers":"use IB Filter above to highlight")+'</p>'
    +'<button onclick="_wmmToggleHeat()" style="font-size:9px;padding:2px 8px;border-radius:3px;cursor:pointer;flex-shrink:0;'+_btnStyle+'">'+(_wmmHeatMode?'\u{1F308} IB Mode':'\u{1F525} Bin Density')+'</button>'
    +'</div>'
    +svgStr
    +_heatLeg
    +'</div>';
  if(el2)el2.innerHTML=el.innerHTML;
  var _cIbs=Object.keys(legSeen).filter(function(k){return k!=="null";}).map(function(k){return+k;});
  if(_cIbs.length){
    var _bIbs=[];
    document.querySelectorAll("#wm-pat-binrow input[data-ib]").forEach(function(inp){_bIbs.push(+inp.dataset.ib);});
    var _mIbs=Array.from(new Set(_bIbs.concat(_cIbs))).sort(function(a,b){return a-b;});
    _wmPatBuildBinRow(_mIbs);
  }
}
function _wmPatInitDrag(){
  var drag=document.getElementById("wm-pat-drag");
  var box=document.getElementById("wm-pat-box");
  if(!drag||!box||box._dragInit)return;
  box._dragInit=true;
  var ox=0,oy=0,bx=0,by=0;
  drag.addEventListener("mousedown",function(e){
    if(e.target.closest&&(e.target.closest("button")||e.target.closest("select")))return;
    ox=e.clientX;oy=e.clientY;
    var r=box.getBoundingClientRect();bx=r.left;by=r.top;
    box.style.left=bx+"px";box.style.top=by+"px";box.style.right="auto";
    function onMove(ev){box.style.left=(bx+ev.clientX-ox)+"px";box.style.top=(by+ev.clientY-oy)+"px";}
    function onUp(){document.removeEventListener("mousemove",onMove);document.removeEventListener("mouseup",onUp);}
    document.addEventListener("mousemove",onMove);
    document.addEventListener("mouseup",onUp);
    e.preventDefault();
  });
}
(function(){
  var resizer=document.getElementById("wm-pat-scores-resize");
  if(!resizer)return;
  resizer.addEventListener("mousedown",function(e){
    var panel=document.getElementById("wm-pat-scores-panel");
    if(!panel)return;
    var startY=e.clientY,startH=panel.getBoundingClientRect().height;
    function onMove(ev){var newH=Math.max(40,startH-(ev.clientY-startY));panel.style.height=newH+"px";}
    function onUp(){document.removeEventListener("mousemove",onMove);document.removeEventListener("mouseup",onUp);}
    document.addEventListener("mousemove",onMove);
    document.addEventListener("mouseup",onUp);
    e.preventDefault();
  });
})();
(function(){
  var vs=document.getElementById("wm-pat-vsplit");
  if(!vs)return;
  vs.addEventListener("mousedown",function(e){
    var left=vs.previousElementSibling;
    var inner=vs.parentElement;
    if(!left||!inner)return;
    var startX=e.clientX,startW=left.getBoundingClientRect().width,innerW=inner.getBoundingClientRect().width;
    function onMove(ev){
      var nw=Math.max(180,Math.min(innerW-8,startW+(ev.clientX-startX)));
      left.style.width=nw+"px";left.style.flex="none";
      var fullyExpanded=(nw>=innerW-8);
      vs.style.display=fullyExpanded?"none":"";
      var right=vs.nextElementSibling;
      if(right){right.style.display=fullyExpanded?"none":"";}
    }
    function onUp(){document.removeEventListener("mousemove",onMove);document.removeEventListener("mouseup",onUp);}
    document.addEventListener("mousemove",onMove);
    document.addEventListener("mouseup",onUp);
    e.preventDefault();
  });
})();
"""

# ── CSS (wm-pat-* classes, matches yield-dashboard _pipeline_html.py) ─────────
WPA_CSS = """
.wm-pat-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.45);z-index:23000;pointer-events:none}
.wm-pat-overlay.open{display:block;pointer-events:none}
.wm-pat-box{position:absolute;left:3vw;top:36px;background:#f0f2f5;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.35);width:94vw;max-width:1400px;height:78vh;min-width:640px;min-height:360px;max-height:95vh;display:flex;flex-direction:column;pointer-events:auto;resize:both;overflow:hidden}
.wm-pat-drag{cursor:move;background:#145a32;color:#fff;padding:8px 14px;border-radius:8px 8px 0 0;display:flex;align-items:center;gap:10px;user-select:none;flex-shrink:0}
.wm-pat-body2{display:flex;flex-direction:column;flex:1;padding:8px;gap:6px;min-height:0;overflow:hidden}
.wm-pat-inner2{display:flex;gap:0;flex:1;min-height:0;overflow:auto}
.wm-pat-left{display:flex;flex-direction:column;gap:6px;flex:none;width:55%;min-width:180px;min-height:0;overflow:hidden}
.wm-pat-vsplit{width:6px;cursor:ew-resize;background:#e0e0e0;flex-shrink:0;display:flex;align-items:center;justify-content:center;border-radius:3px;margin:0 1px}
.wm-pat-vsplit:hover{background:#bbb}
.wm-pat-vsplit::after{content:"";width:2px;height:30px;background:#999;border-radius:1px}
.wm-pat-maps-wrap{overflow:auto;background:#fff;border-radius:6px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex:1;min-height:0}
.wm-pat-maps{display:flex;flex-wrap:wrap;gap:10px}
.wm-pat-ltab-bar{display:flex;gap:0;border-bottom:2px solid #d5d8dc;flex-shrink:0;margin-bottom:4px}
.wm-pat-ltab{font-size:11px;padding:3px 10px;cursor:pointer;border:1px solid transparent;border-bottom:none;border-radius:4px 4px 0 0;color:#666;background:none;white-space:nowrap}
.wm-pat-ltab.on{border-color:#d5d8dc;background:#fff;color:#145a32;font-weight:bold;margin-bottom:-2px}
.wm-pat-lpane{display:none;flex:1;min-height:0;overflow:auto}
.wm-pat-lpane.on{display:flex;flex-direction:column}
.wm-pat-right{display:flex;flex-direction:column;gap:4px;flex:1;min-width:200px;min-height:0;overflow:hidden}
.wm-pat-scores{flex:0 0 auto;background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);display:flex;flex-direction:column;overflow:hidden}
.wm-pat-scores-resize{height:5px;background:#e0e0e0;cursor:ns-resize;flex-shrink:0;border-radius:2px;margin:1px 0;display:flex;align-items:center;justify-content:center}
.wm-pat-scores-resize:hover{background:#bbb}
.wm-pat-scores-resize::after{content:"";width:30px;height:2px;background:#999;border-radius:1px}
.wm-pat-tbl-wrap{overflow:auto;flex:1;min-height:0;padding:4px}
.wm-t{border-collapse:collapse;font-size:11px;width:100%}
.wm-t th{background:#145a32;color:#fff;padding:4px 8px;text-align:left;position:sticky;top:0;z-index:1;white-space:nowrap}
.wm-t td{padding:3px 8px;border-bottom:1px solid #eee}
.wm-t tr:nth-child(even) td{background:#f7f9fc}
.wm-bar-bg{background:#e8e8e8;border-radius:3px;height:8px;width:90px;display:inline-block;vertical-align:middle}
.wm-bar-fg{height:8px;border-radius:3px;display:block}
.wm-impact-row{display:flex;align-items:center;gap:6px;font-size:11px;margin-bottom:2px}
.wm-impact-lbl{width:80px;min-width:80px;text-align:right;font-weight:bold;white-space:nowrap;flex-shrink:0}
.wm-impact-bar{flex:1;background:#e8e8e8;border-radius:3px;height:8px;position:relative}
.wm-impact-fill{height:8px;border-radius:3px;position:absolute;left:0;top:0}
.wm-pat-impact{background:#fff;border-radius:5px;padding:5px 8px;box-shadow:0 1px 3px rgba(0,0,0,.1);flex:1;overflow:auto;min-height:0}
.wm-pat-ctrl{display:flex;align-items:flex-start;gap:8px;flex-shrink:0;padding:2px 0;flex-wrap:wrap}
.wm-pat-binrow{display:flex;flex-wrap:wrap;gap:3px 6px;font-size:11px;padding:3px 6px;background:#fff;border-radius:5px;box-shadow:0 1px 3px rgba(0,0,0,.1);flex-shrink:0}
.wm-pat-bincb{display:flex;align-items:center;gap:3px;cursor:pointer;padding:1px 3px;border-radius:3px;white-space:nowrap}
.wm-pat-bincb:hover{background:#f0f4fa}
.wm-pat-bincb input{cursor:pointer;margin:0}
.wm-pat-binsw{width:10px;height:10px;border-radius:2px;flex-shrink:0;display:inline-block}
.wm-pat-tabs{display:flex;gap:0;border-bottom:2px solid #d5d8dc;flex-shrink:0}
.wm-pat-tab{font-size:11px;padding:4px 12px;cursor:pointer;border:1px solid transparent;border-bottom:none;border-radius:5px 5px 0 0;color:#666;background:none;white-space:nowrap}
.wm-pat-tab.on{border-color:#d5d8dc;background:#fff;color:#145a32;font-weight:bold;margin-bottom:-2px}
.wm-pat-tabpane{display:none;flex:1;min-height:0;overflow:auto}
.wm-pat-tabpane.on{display:flex;flex-direction:column}
.wm-wlbl{font-size:11px;font-weight:bold;color:#2c3e50;text-align:center;margin-bottom:3px}
.wm-pat-close{background:none;border:none;color:#a9dfbf;cursor:pointer;font-size:20px;line-height:1;padding:0}
.wm-pat-close:hover{color:#fff}
.wm-pat-btn{font-size:11px;padding:3px 10px;background:#27ae60;color:#fff;border:none;border-radius:3px;cursor:pointer;white-space:nowrap}
.wm-pat-btn:hover{background:#1e8449}
"""
