"""_tab_summ.py — Parameter Table: unified SICC+CDYN with sort/search/checkbox/dist icons."""

TAB_ID     = 'tab-summ'
TAB_LABEL  = 'Parameter Table'


def tab_html() -> str:
    return ''


def tab_js() -> str:
    return '''
/* ── Parameter Table state ──────────────────────────────────────────── */
var _ptRows=[];        // [{type,cat,testName,dispName,upmCol,isCdyn,groupKey}, ...]
var _ptSortCol=null;
var _ptSortDir=1;
var _ptQuery='';
var _ptSelSet=new Set();

/* 8 distinct group colors: 4 cat slots × SICC/CDYN */
var _PT_COLORS=[
  ['#dbeafe','#1e40af'],['#dcfce7','#166534'],  /* cat0: SICC=light-blue, CDYN=light-green */
  ['#fef9c3','#854d0e'],['#fce7f3','#9d174d'],  /* cat1: SICC=light-yellow, CDYN=light-pink */
  ['#ede9fe','#5b21b6'],['#ffedd5','#9a3412'],  /* cat2: SICC=light-violet, CDYN=light-orange */
  ['#e0f2fe','#075985'],['#f0fdf4','#14532d']   /* cat3: more blues/greens */
];

function _ptGroupColor(catIdx,isCdyn){
  var pair=_PT_COLORS[catIdx%(_PT_COLORS.length/2|0)*2+(isCdyn?1:0)];
  return pair||['#f8f9fa','#374151'];
}

function _ptBuildRows(){
  _ptRows=[];
  var catMap={},catIdx=0;
  /* SICC rows */
  (SICC_TBL_CFG||[]).forEach(function(r){
    var cat=r[0];
    if(!(cat in catMap))catMap[cat]=catIdx++;
    _ptRows.push({type:'SICC',cat:cat,testName:r[2],dispName:r[1]||r[2],upmCol:r[3]||'',isCdyn:false,catIdx:catMap[cat]});
  });
  /* CDYN rows */
  (CDYN_TBL_CFG||[]).forEach(function(r){
    var cat=r[0];
    if(!(cat in catMap))catMap[cat]=catIdx++;
    _ptRows.push({type:'CDYN',cat:cat,testName:r[2],dispName:r[1]||r[2],upmCol:r[3]||'',isCdyn:true,catIdx:catMap[cat]});
  });
  /* fallback: raw SICC cols */
  if(!_ptRows.length){
    SICC_COLS.forEach(function(c){_ptRows.push({type:'SICC',cat:'SICC',testName:c,dispName:c,upmCol:'',isCdyn:false,catIdx:0});});
    CDYN_COLS.forEach(function(c){_ptRows.push({type:'CDYN',cat:'CDYN',testName:c,dispName:c,upmCol:'',isCdyn:true,catIdx:1});});
  }
  _ptSelSet=new Set(); /* will be populated by _ptSyncFromSicc below */
}

function _ptComputeRow(row,ai){
  var actual=null,tgt=null,ratio=null,upmMed=null,upmTgt=null;
  if(row.isCdyn){
    var v=ai.map(function(i){return ROWS[i].cdyn[row.testName];}).filter(function(v){return v!=null&&!isNaN(v);});
    actual=medArr(v); tgt=CDYN_TARGETS[row.testName]||null;
  }else{
    var v=ai.map(function(i){return ROWS[i].medians[row.testName];}).filter(function(v){return v!=null&&!isNaN(v);});
    actual=medArr(v); tgt=TARGETS[row.testName.toUpperCase()]||null;
  }
  ratio=(actual!=null&&tgt!=null&&tgt!==0)?actual/tgt:null;
  if(row.upmCol){
    var uv=ai.map(function(i){return ROWS[i].medians[row.upmCol];}).filter(function(v){return v!=null&&!isNaN(v);});
    upmMed=medArr(uv); upmTgt=TARGETS[row.upmCol.toUpperCase()]||null;
  }
  return{actual:actual,tgt:tgt,ratio:ratio,upmMed:upmMed,upmTgt:upmTgt};
}

function _ptRender(){
  var hd=document.getElementById('param-tbl-head'),bd=document.getElementById('param-tbl-body');
  if(!hd||!bd)return;
  var ai=SEL_WFR.size>0?Array.from(SEL_WFR):getFiltered();
  /* Compute values for all rows */
  var computed=_ptRows.map(function(r){return _ptComputeRow(r,ai);});
  /* Sort */
  var idxArr=_ptRows.map(function(_,i){return i;});
  if(_ptSortCol!==null){
    idxArr.sort(function(a,b){
      var va=_ptSortVal(a,computed[a]),vb=_ptSortVal(b,computed[b]);
      if(va===null&&vb===null)return 0;
      if(va===null)return 1;if(vb===null)return -1;
      return _ptSortDir*(va<vb?-1:va>vb?1:0);
    });
  }
  /* Header */
  var cols=[
    {k:'sel',l:'',w:'28px'},
    {k:'actions',l:'',w:'44px'},
    {k:'type',l:'Type',w:'46px'},
    {k:'cat',l:'Category',w:'80px'},
    {k:'test',l:'Parameter',w:'160px'},
    {k:'actual',l:'Median',w:'72px'},
    {k:'tgt',l:'Target',w:'72px'},
    {k:'ratio',l:'Ratio',w:'52px'},
    {k:'upm',l:'UPM%',w:'52px'},
    {k:'upmtgt',l:'UPM Tgt',w:'60px'}
  ];
  var th='background:#2c3e50;color:#fff;padding:4px 7px;font-size:11px;position:sticky;top:0;z-index:2;cursor:pointer;user-select:none;white-space:nowrap';
  var hdrHtml='<tr>';
  cols.forEach(function(c){
    if(c.k==='sel'){
      hdrHtml+='<th style="'+th+';cursor:default;width:'+c.w+'"><input type="checkbox" id="pt-sel-all" onmousedown="this._wasIndet=this.indeterminate" onclick="_ptHdrClick(this)" style="cursor:pointer"></th>';
    }else if(c.k==='actions'){
      hdrHtml+='<th style="'+th+';cursor:default;width:'+c.w+'">'+c.l+'</th>';
    }else{
      var arrow=_ptSortCol===c.k?(_ptSortDir>0?' &#9650;':' &#9660;'):'';
      hdrHtml+='<th style="'+th+';width:'+c.w+'" data-sk="'+c.k+'" onclick="_ptSort(this.dataset.sk)">'+c.l+arrow+'</th>';
    }
  });
  hdrHtml+='</tr>';
  hd.innerHTML=hdrHtml;
  /* Restore header checkbox state */
  var allCb=document.getElementById('pt-sel-all');
  if(allCb){
    var visCount=0,selCount=0;
    idxArr.forEach(function(i){
      var row=_ptRows[i];
      var q=_ptQuery.toLowerCase();
      if(q&&(row.dispName+row.cat+row.type+row.testName).toLowerCase().indexOf(q)<0)return;
      visCount++;if(_ptSelSet.has(i))selCount++;
    });
    allCb.checked=visCount>0&&selCount===visCount;
    allCb.indeterminate=selCount>0&&selCount<visCount;
  }
  /* Body */
  var q=_ptQuery.toLowerCase();
  var body='';
  idxArr.forEach(function(i){
    var row=_ptRows[i],cv=computed[i];
    /* search filter */
    if(q&&(row.dispName+row.cat+row.type+row.testName).toLowerCase().indexOf(q)<0)return;
    var clr=_ptGroupColor(row.catIdx,row.isCdyn);
    var bg=clr[0],fg=clr[1];
    var sel=_ptSelSet.has(i);
    var over=cv.ratio!=null&&cv.ratio>1,warn=cv.ratio!=null&&cv.ratio>0.95&&cv.ratio<=1;
    var medBg=over?'background:#fdecea':warn?'background:#fef9e7':'background:'+bg;
    var ratioBg=over?'background:#fdecea;color:#c0392b;font-weight:bold':warn?'background:#fef9e7':'';
    var td='padding:3px 7px;border-bottom:1px solid rgba(0,0,0,.06);vertical-align:middle';
    body+='<tr data-idx="'+i+'" style="background:'+bg+'">'
      +'<td style="'+td+';text-align:center;width:28px"><input type="checkbox" '+(sel?'checked':'')+' onchange="_ptCheckRow('+i+',this.checked)" style="cursor:pointer"></td>'
      +'<td style="'+td+';text-align:center;width:44px;white-space:nowrap">'
        +'<button onclick="_ptShowDist('+i+',false)" title="Distribution" style="background:none;border:none;cursor:pointer;font-size:14px;padding:0 1px">&#128202;</button>'
        +'<button onclick="_ptShowDist('+i+',true)" title="UPM" style="background:none;border:none;cursor:pointer;font-size:14px;padding:0 1px">&#128200;</button>'
      +'</td>'
      +'<td style="'+td+';color:'+fg+';font-weight:bold;font-size:10px;text-align:center">'+esc(row.type)+'</td>'
      +'<td style="'+td+';color:'+fg+';font-size:10px">'+esc(row.cat)+'</td>'
      +'<td style="'+td+';font-weight:bold;border-left:3px solid '+fg+'">'+esc(row.dispName)+'</td>'
      +'<td style="'+td+';text-align:right;'+medBg+'">'+(cv.actual!=null?cv.actual.toFixed(4):'&#8212;')+'</td>'
      +'<td style="'+td+';text-align:right">'+(cv.tgt!=null?cv.tgt.toFixed(4):'&#8212;')+'</td>'
      +'<td style="'+td+';text-align:right;'+ratioBg+'">'+(cv.ratio!=null?cv.ratio.toFixed(2):'&#8212;')+'</td>'
      +'<td style="'+td+';text-align:right">'+(cv.upmMed!=null?cv.upmMed.toFixed(2):'&#8212;')+'</td>'
      +'<td style="'+td+';text-align:right">'+(cv.upmTgt!=null?cv.upmTgt.toFixed(2):'&#8212;')+'</td>'
      +'</tr>';
  });
  if(!body)body='<tr><td colspan="10" style="padding:14px;color:#7f8c8d;text-align:center">No data.</td></tr>';;
  bd.innerHTML=body;
}

function _ptSortVal(i,cv){
  var row=_ptRows[i];
  if(_ptSortCol==='type')return row.type;
  if(_ptSortCol==='cat')return row.cat;
  if(_ptSortCol==='test')return row.dispName;
  if(_ptSortCol==='actual')return cv.actual;
  if(_ptSortCol==='tgt')return cv.tgt;
  if(_ptSortCol==='ratio')return cv.ratio;
  if(_ptSortCol==='upm')return cv.upmMed;
  if(_ptSortCol==='upmtgt')return cv.upmTgt;
  return null;
}

/* Derive _ptSelSet entirely from SICC_CHECKED_ROWS — single source of truth */
function _ptSyncFromSicc(){
  if(typeof _siccAllRowKeys==='undefined'||typeof SICC_CHECKED_ROWS==='undefined')return;
  _ptRows.forEach(function(row,i){
    var anyChecked=_siccAllRowKeys.some(function(k){
      return k.indexOf(row.testName+'||')===0&&SICC_CHECKED_ROWS.has(k);
    });
    if(anyChecked)_ptSelSet.add(i);else _ptSelSet.delete(i);
  });
}
window._ptSyncFromSicc=_ptSyncFromSicc;
function _ptSort(col){
  if(_ptSortCol===col)_ptSortDir*=-1;
  else{_ptSortCol=col;_ptSortDir=1;}
  _ptRender();
}

function _ptFilter(q){
  _ptQuery=q;
  _ptRender();
}

function _ptToggleAll(checked){
  /* Update SEL_COLS and SICC_CHECKED_ROWS for all rows of active mode, then mirror back */
  var isCdyn=(typeof _siccScatterMode!=='undefined')&&_siccScatterMode==='cdyn';
  if(typeof _siccAllRowKeys!=='undefined'&&typeof SICC_CHECKED_ROWS!=='undefined'&&
     typeof SICC_SEL_COLS!=='undefined'&&typeof CDYN_SEL_COLS!=='undefined'){
    var sc=isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS;
    _ptRows.forEach(function(row){
      if(row.isCdyn!==isCdyn)return;
      if(checked)sc.add(row.testName);else sc.delete(row.testName);
      var found=false;
      _siccAllRowKeys.forEach(function(k){
        if(k.indexOf(row.testName+'||')===0){
          found=true;
          if(checked)SICC_CHECKED_ROWS.add(k);else SICC_CHECKED_ROWS.delete(k);
        }
      });
      if(!found&&checked){
        var dk=row.testName+'||All';
        _siccAllRowKeys.push(dk);
        if(typeof _siccAllRowKeysSet!=='undefined')_siccAllRowKeysSet.add(dk);
        SICC_CHECKED_ROWS.add(dk);
      }
    });
  }
  _ptSyncFromSicc();
  if(!checked){
    /* Fast path for deselect-all: restyle all traces + target lines invisible.
       Do NOT call render_upm_dist (it would auto-repopulate SICC_SEL_COLS). */
    var _el=typeof _siccTraceIndexMap!=='undefined'&&document.getElementById('sicc-scatter-div');
    if(_el&&_el._spl&&typeof _siccTraceIndexMap!=='undefined'){
      var _allIdxs=[];
      Object.keys(_siccTraceIndexMap).forEach(function(k){_siccTraceIndexMap[k].forEach(function(i){_allIdxs.push(i);});});
      if(typeof _siccTargetTraceIndices!=='undefined')_siccTargetTraceIndices.forEach(function(i){_allIdxs.push(i);});
      if(_allIdxs.length)Plotly.restyle(_el,{visible:false},_allIdxs);
    }
    /* Clear the stats table — no rows should show when nothing is selected */
    var _bd=document.getElementById('sicc-stats-body');
    if(_bd){_bd.innerHTML='';}
    var _hcb=document.getElementById('sicc-sel-all');
    if(_hcb){_hcb.checked=false;_hcb.indeterminate=false;}
  }else{
    if(typeof render_upm_dist!=='undefined')render_upm_dist();
  }
  _ptRender();
}

function _ptCheckRow(i,checked){
  var row=_ptRows[i];if(!row)return;
  var isCdyn=row.isCdyn;
  /* Add/remove from SEL_COLS so render_upm_dist picks it up */
  if(typeof SICC_SEL_COLS!=='undefined'&&typeof CDYN_SEL_COLS!=='undefined'){
    var sc=isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS;
    if(checked)sc.add(row.testName);else sc.delete(row.testName);
  }
  /* Update SICC_CHECKED_ROWS for all known group keys */
  if(typeof _siccAllRowKeys!=='undefined'&&typeof SICC_CHECKED_ROWS!=='undefined'){
    var found=false;
    _siccAllRowKeys.forEach(function(k){
      if(k.indexOf(row.testName+'||')===0){
        found=true;
        if(checked)SICC_CHECKED_ROWS.add(k);else SICC_CHECKED_ROWS.delete(k);
      }
    });
    /* If no keys registered yet, pre-populate the default 'All' group key */
    if(!found&&checked){
      var dk=row.testName+'||All';
      _siccAllRowKeys.push(dk);
      if(typeof _siccAllRowKeysSet!=='undefined')_siccAllRowKeysSet.add(dk);
      SICC_CHECKED_ROWS.add(dk);
    }
  }
  _ptSyncFromSicc();
  if(typeof render_upm_dist!=='undefined')render_upm_dist();
}

function _ptCloseModal(){var m=document.getElementById('pt-dist-modal');if(m)m.style.display='none';}
window._ptCloseModal=_ptCloseModal;
function _ptShowDist(rowIdx,showUpm){
  var row=_ptRows[rowIdx];
  if(!row)return;
  var testName=row.testName,isCdyn=row.isCdyn;
  var ai=getFiltered();var active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  /* Build/show floating modal */
  var modal=document.getElementById('pt-dist-modal');
  if(!modal){
    modal=document.createElement('div');
    modal.id='pt-dist-modal';
    modal.style.cssText='position:fixed;top:60px;right:20px;width:620px;height:520px;min-width:320px;min-height:260px;max-width:95vw;max-height:92vh;background:#fff;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.35);z-index:9999;display:flex;flex-direction:column;overflow:hidden;resize:both';
    modal.innerHTML='<div id="pt-dist-modal-header" style="display:flex;justify-content:space-between;align-items:center;padding:8px 14px;background:#2c3e50;color:#fff;flex-shrink:0;cursor:grab;user-select:none">'
        +'<span id="pt-dist-modal-title" style="font-size:13px;font-weight:bold"></span>'
        +'<button onclick="_ptCloseModal()" style="background:none;border:none;color:#fff;font-size:18px;cursor:pointer;padding:0 4px">&#10005;</button>'
      +'</div>'
      +'<div style="padding:12px;overflow:auto;flex:1;display:flex;flex-direction:column;min-height:0">'
        +'<div id="pt-dist-modal-content" style="flex:1;display:flex;flex-direction:column;min-height:0"></div>'
      +'</div>';
    document.body.appendChild(modal);
    /* Drag logic */
    (function(){
      var hdr=document.getElementById('pt-dist-modal-header');
      var dx=0,dy=0,mx=0,my=0;
      hdr.addEventListener('mousedown',function(e){
        if(e.target.tagName==='BUTTON')return;
        e.preventDefault();
        mx=e.clientX;my=e.clientY;
        document.addEventListener('mousemove',onDrag);
        document.addEventListener('mouseup',onUp);
        hdr.style.cursor='grabbing';
      });
      function onDrag(e){
        dx=e.clientX-mx;dy=e.clientY-my;mx=e.clientX;my=e.clientY;
        modal.style.left=(modal.offsetLeft+dx)+'px';
        modal.style.top=(modal.offsetTop+dy)+'px';
        modal.style.right='auto';
      }
      function onUp(){document.removeEventListener('mousemove',onDrag);document.removeEventListener('mouseup',onUp);hdr.style.cursor='grab';}
    })();
  }
  window.pt_modal_id='pt-dist-modal';
  modal._ptRowIdx=rowIdx;
  modal._ptShowUpm=showUpm;
  var titleEl=document.getElementById('pt-dist-modal-title');
  if(titleEl)titleEl.textContent=(showUpm?'UPM Distribution':'Distribution')+': '+testName;
  /* Inject SVG container into modal */
  var content=document.getElementById('pt-dist-modal-content');
  if(showUpm){
    content.innerHTML='<svg id="pt-modal-upm-svg" style="width:100%;flex:1;min-height:0;display:block;border:1px solid #f5e0c3;border-radius:4px;background:#fffaf4"></svg><div id="pt-modal-upm-note" style="font-size:10px;color:#c0650a;margin-top:3px"></div><div id="pt-modal-upm-stats" style="margin-top:6px"></div>';
    modal.style.display='flex';
    if(typeof drawMiniUpm!=='undefined')drawMiniUpm(active,testName,isCdyn,'pt-modal-upm-svg','pt-dist-modal-title','pt-modal-upm-note');
  }else{
    content.innerHTML='<h3 id="pt-modal-dist-title" style="font-size:12px;color:#2c3e50;margin:0 0 4px"></h3><svg id="pt-modal-hist-svg" style="width:100%;flex:1;min-height:0;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg><div id="pt-modal-chart-note" style="font-size:11px;color:#7f8c8d;margin-top:4px"></div><div id="pt-modal-stats-tbl" style="margin-top:8px"></div>';
    modal.style.display='flex';
    if(typeof _renderSiccHistOnly!=='undefined'){
      var _origHistSvg='upm-hist-svg',_origHistNote='upm-chart-note',_origStsTbl='upm-stats-tbl',_origTitle='sicc-dist-title';
      /* Temporarily swap IDs so _renderSiccHistOnly writes into modal SVG */
      var h=document.getElementById(_origHistSvg),hn=document.getElementById(_origHistNote),hs=document.getElementById(_origStsTbl),ht=document.getElementById(_origTitle);
      var mh=document.getElementById('pt-modal-hist-svg'),mhn=document.getElementById('pt-modal-chart-note'),mhs=document.getElementById('pt-modal-stats-tbl'),mht=document.getElementById('pt-modal-dist-title');
      if(h)h.id='_ph_hist';if(hn)hn.id='_ph_note';if(hs)hs.id='_ph_stbl';if(ht)ht.id='_ph_ttl';
      if(mh)mh.id=_origHistSvg;if(mhn)mhn.id=_origHistNote;if(mhs)mhs.id=_origStsTbl;if(mht)mht.id=_origTitle;
      _renderSiccHistOnly(active,testName,isCdyn);
      if(mh)mh.id='pt-modal-hist-svg';if(mhn)mhn.id='pt-modal-chart-note';if(mhs)mhs.id='pt-modal-stats-tbl';if(mht)mht.id='pt-modal-dist-title';
      if(h)h.id=_origHistSvg;if(hn)hn.id=_origHistNote;if(hs)hs.id=_origStsTbl;if(ht)ht.id=_origTitle;
    }
  }
}
window._ptShowDist=_ptShowDist;
/* Export parameter table as CSV */
function _ptExportCsv(){
  if(!_ptRows.length)_ptBuildRows();
  var ai=SEL_WFR.size>0?Array.from(SEL_WFR):getFiltered();
  var computed=_ptRows.map(function(r){return _ptComputeRow(r,ai);});
  var q=_ptQuery.toLowerCase();
  var hdrs=['Type','Category','Parameter','Median','Target','Ratio','UPM%','UPM_Target','Checked'];
  var lines=[hdrs.join(',')];
  _ptRows.forEach(function(row,i){
    if(q&&(row.dispName+row.cat+row.type+row.testName).toLowerCase().indexOf(q)<0)return;
    var cv=computed[i];
    var checked=_ptSelSet.has(i)?'1':'0';
    var vals=[row.type,row.cat,row.dispName,
      cv.actual!=null?cv.actual.toFixed(6):'',
      cv.tgt!=null?cv.tgt.toFixed(6):'',
      cv.ratio!=null?cv.ratio.toFixed(4):'',
      cv.upmMed!=null?cv.upmMed.toFixed(4):'',
      cv.upmTgt!=null?cv.upmTgt.toFixed(4):'',
      checked];
    lines.push(vals.map(function(v){var s=String(v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}).join(','));
  });
  var blob=new Blob([lines.join(String.fromCharCode(13,10))],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='parameter_table.csv';document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},100);
}
window._ptExportCsv=_ptExportCsv;
/* Refresh the open dist/upm popup when lot/wafer selection changes */
function _ptRefreshModal(){
  var modal=document.getElementById('pt-dist-modal');
  if(!modal||modal.style.display==='none')return;
  var rowIdx=modal._ptRowIdx;
  var showUpm=modal._ptShowUpm;
  if(rowIdx==null||!_ptRows[rowIdx])return;
  _ptShowDist(rowIdx,showUpm);
}
window._ptRefreshModal=_ptRefreshModal;
window._ptSort=_ptSort;
window._ptFilter=_ptFilter;
window._ptToggleAll=_ptToggleAll;
function _ptHdrClick(cb){
  /* onmousedown captured the pre-click indeterminate state;
     if it was indeterminate ('-'), treat click as deselect-all regardless of
     what the browser set checked to. */
  var deselect=cb._wasIndet===true||!cb.checked;
  cb.indeterminate=false;
  cb.checked=!deselect;
  delete cb._wasIndet;
  _ptToggleAll(!deselect);
}
window._ptHdrClick=_ptHdrClick;
window._ptCheckRow=_ptCheckRow;

function toggleSummPanel(){
  var panel=document.getElementById('all-med-panel');
  var btn=document.getElementById('all-med-toggle-btn');
  if(!panel)return;
  var open=panel.classList.contains('open');
  if(open){
    panel._savedW=panel.getBoundingClientRect().width;
    panel.style.flex='';panel.style.width='';
    panel.classList.remove('open');
    if(btn)btn.innerHTML='&#9654;';
  }else{
    panel.classList.add('open');
    var w=panel._savedW||480;
    panel.style.flex='0 0 '+w+'px';panel.style.width=w+'px';
    if(btn)btn.innerHTML='&#9664;';
    render_summ();
  }
}
function render_summ_if_open(){
  var panel=document.getElementById('all-med-panel');
  if(panel&&panel.classList.contains('open'))render_summ();
}
function render_summ(){
  if(!_ptRows.length)_ptBuildRows();
  _ptSyncFromSicc(); /* always mirror SICC_CHECKED_ROWS before rendering */
  _ptRender();
}
window.toggleSummPanel=toggleSummPanel;
'''

    return '''
function toggleSummPanel(){
  var panel=document.getElementById('all-med-panel');
  var btn=document.getElementById('all-med-toggle-btn');
  if(!panel)return;
  var open=panel.classList.contains('open');
  if(open){
    panel._savedW=panel.getBoundingClientRect().width;
    panel.style.flex='';panel.style.width='';
    panel.classList.remove('open');
    if(btn)btn.innerHTML='&#9654;';
  }else{
    panel.classList.add('open');
    var w=panel._savedW||420;
    panel.style.flex='0 0 '+w+'px';panel.style.width=w+'px';
    if(btn)btn.innerHTML='&#9664;';
    render_summ();
  }
}
function render_summ_if_open(){
  var panel=document.getElementById('all-med-panel');
  if(panel&&panel.classList.contains('open'))render_summ();
}
function render_summ(){
  var ai=SEL_WFR.size>0?Array.from(SEL_WFR):getFiltered();
  _renderCatTable(SICC_TBL_CFG,ai,false,'sicc-cat-head','sicc-cat-body','sicc-cat-legend',SUMM_SICC_OFF);
  _renderCatTable(CDYN_TBL_CFG,ai,true,'cdyn-cat-head','cdyn-cat-body','cdyn-cat-legend',SUMM_CDYN_OFF);
}

function _renderCatTable(cfg,ai,isCdyn,headId,bodyId,legendId,offSet){
  var headEl=document.getElementById(headId);
  var bodyEl=document.getElementById(bodyId);
  var legEl =document.getElementById(legendId);
  if(!cfg||!cfg.length){
    if(headEl)headEl.innerHTML='';
    if(bodyEl)bodyEl.innerHTML='<tr><td colspan="5" style="padding:14px;color:#7f8c8d">No table config defined.</td></tr>';
    if(legEl)legEl.innerHTML='';
    return;
  }
  var catOrder=[],catSet=new Set();
  cfg.forEach(function(row){
    if(!catSet.has(row[0])){catSet.add(row[0]);catOrder.push(row[0]);}
  });
  if(legEl) _buildCatLegend(catOrder,offSet,legendId,render_summ);
  var hdr='<tr><th style="text-align:left;min-width:160px">Test</th><th>Cat</th><th>Median</th><th>Target</th><th>Ratio</th><th>UPM%</th><th>UPM Tgt</th></tr>';
  if(headEl)headEl.innerHTML=hdr;
  var body='',lastCat='';
  cfg.forEach(function(row){
    var cat=row[0],dispName=row[1],testName=row[2],upmCol=row[3];
    if(offSet&&offSet.has(cat))return;
    if(cat!==lastCat){
      body+='<tr class="cat-hdr"><td colspan="7" style="background:'+_catColor(cat)+';color:'+_catBorder(cat)+';border-left:4px solid '+_catBorder(cat)+'">'+esc(cat)+'</td></tr>';
      lastCat=cat;
    }
    var actual=null,tgt=null,ratio=null,upmMed=null,upmTgt=null;
    if(isCdyn){
      var vals=ai.map(function(i){return ROWS[i].cdyn[testName];}).filter(function(v){return v!=null&&!isNaN(v);});
      actual=medArr(vals); tgt=CDYN_TARGETS[testName]||null;
    }else{
      var vals=ai.map(function(i){return ROWS[i].medians[testName];}).filter(function(v){return v!=null&&!isNaN(v);});
      actual=medArr(vals); tgt=TARGETS[testName.toUpperCase()]||null;
    }
    ratio=(actual!=null&&tgt!=null&&tgt!==0)?actual/tgt:null;
    if(upmCol){
      var uv=ai.map(function(i){return ROWS[i].medians[upmCol];}).filter(function(v){return v!=null&&!isNaN(v);});
      upmMed=medArr(uv); upmTgt=TARGETS[upmCol.toUpperCase()]||null;
    }
    var bg=_catColor(cat);
    body+='<tr style="background:'+bg+'">';
    body+='<td style="text-align:left;border-left:4px solid '+_catBorder(cat)+'">'+esc(dispName)+'</td>';
    body+='<td style="color:#7f8c8d;font-size:10px;text-align:center">'+esc(cat)+'</td>';
    body+='<td class="'+ccls(actual,tgt,isCdyn)+'">'+(actual!=null?actual.toFixed(4):'&#8212;')+'</td>';
    body+='<td class="tgt">'+(tgt!=null?tgt.toFixed(4):'&#8212;')+'</td>';
    body+='<td class="'+ratioCls(ratio)+'">'+(ratio!=null?ratio.toFixed(2):'&#8212;')+'</td>';
    body+='<td class="'+upmCls(upmMed,upmTgt)+'">'+(upmMed!=null?upmMed.toFixed(2):'&#8212;')+'</td>';
    body+='<td class="tgt">'+(upmTgt!=null?upmTgt.toFixed(2):'&#8212;')+'</td>';
    body+='</tr>';
  });
  if(bodyEl)bodyEl.innerHTML=body;
}
window.toggleSummPanel=toggleSummPanel;
'''
