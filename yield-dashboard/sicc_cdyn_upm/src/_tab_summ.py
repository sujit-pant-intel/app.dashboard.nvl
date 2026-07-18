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
var _ptPopupGroupBy=['material']; // groupby state for popup plots

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
        +'<button onclick="_ptShowDist('+i+',true)" title="UPM Distribution" style="background:none;border:none;cursor:pointer;font-size:14px;padding:0 1px">&#9889;</button>'
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

/* ── Popup groupby controls ─────────────────────────────────────────── */
function _ptGroupByHTML(){
  var opts=['program','lot','wafer','material'];
  var none=_ptPopupGroupBy.length===0;
  var html='<div style="margin:0 0 8px;font-size:11px;color:#555;border-bottom:1px solid #eee;padding-bottom:6px;display:flex;flex-wrap:wrap;align-items:center;gap:6px">'
    +'<span style="font-weight:600;margin-right:2px">Group by:</span>'
    +'<label style="cursor:pointer"><input type="checkbox" class="pt-popup-gb" value="none"'+(none?' checked':'')+' onchange="_togglePopupGroup(&apos;none&apos;)"> None</label>';
  opts.forEach(function(o){
    var chk=_ptPopupGroupBy.indexOf(o)>=0?' checked':'';
    html+='<label style="cursor:pointer"><input type="checkbox" class="pt-popup-gb" value="'+o+'"'+chk+' onchange="_togglePopupGroup(&apos;'+o+'&apos;)"> '+o.charAt(0).toUpperCase()+o.slice(1)+'</label>';
  });
  html+='</div>';
  return html;
}
function _togglePopupGroup(field){
  if(field==='none'){_ptPopupGroupBy=[];}
  else{var idx=_ptPopupGroupBy.indexOf(field);if(idx>=0)_ptPopupGroupBy.splice(idx,1);else _ptPopupGroupBy.push(field);}
  document.querySelectorAll('.pt-popup-gb').forEach(function(cb){
    if(cb.value==='none')cb.checked=_ptPopupGroupBy.length===0;
    else cb.checked=_ptPopupGroupBy.indexOf(cb.value)>=0;
  });
  _ptRefreshModal();
}
window._togglePopupGroup=_togglePopupGroup;
/* ── Re-render dist histogram + aggregate stats for given active set ─────── */
function _ptRedrawHistModal(active,testName,isCdyn){
  if(typeof _renderSiccHistOnly==='undefined')return;
  var _o='upm-hist-svg',_on='upm-chart-note',_os='upm-stats-tbl',_ot='sicc-dist-title';
  var h=document.getElementById(_o),hn=document.getElementById(_on),hs=document.getElementById(_os),ht=document.getElementById(_ot);
  var mh=document.getElementById('pt-modal-hist-svg'),mhn=document.getElementById('pt-modal-chart-note'),mhs=document.getElementById('pt-modal-stats-tbl'),mht=document.getElementById('pt-modal-dist-title');
  if(h)h.id='_ph_hist';if(hn)hn.id='_ph_note';if(hs)hs.id='_ph_stbl';if(ht)ht.id='_ph_ttl';
  if(mh)mh.id=_o;if(mhn)mhn.id=_on;if(mhs)mhs.id=_os;if(mht)mht.id=_ot;
  _renderSiccHistOnly(active,testName,isCdyn);
  if(mh)mh.id='pt-modal-hist-svg';if(mhn)mhn.id='pt-modal-chart-note';if(mhs)mhs.id='pt-modal-stats-tbl';if(mht)mht.id='pt-modal-dist-title';
  if(h)h.id=_o;if(hn)hn.id=_on;if(hs)hs.id=_os;if(ht)ht.id=_ot;
  /* Inject full UPM stats columns */
  var _stEl=document.getElementById('pt-modal-stats-tbl');
  if(!_stEl)return;
  var _tbl=_stEl.querySelector('table');
  if(!_tbl)return;
  _tbl.style.width='auto';
  var _extra=_stEl.querySelector('div');if(_extra)_extra.remove();
  var _extra2=_stEl.querySelector('table:nth-of-type(2)');if(_extra2){var _p2=_extra2.parentNode;if(_p2)_p2.remove();}
  var _uCol=_getUpmCol(testName);if(!_uCol)return;
  var _uVals=[];
  active.forEach(function(i){var dp=ROWS[i].die_pairs&&ROWS[i].die_pairs[testName];if(dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))_uVals.push(v);});});
  var _uSt=computeStats(_uVals);if(!_uSt)return;
  var _thBase='padding:6px 10px;font-size:11px;font-weight:600;text-align:center;letter-spacing:0.04em;white-space:nowrap;border-right:1px solid #a04000';
  var _thU=_thBase+';background:#c0650a;color:#fff;font-weight:700';
  var _thUM=_thBase+';background:#9a3412;color:#fff;font-weight:700';
  var _tdBase='padding:6px 10px;font-size:12px;text-align:center;white-space:nowrap;border-right:1px solid #f5d5b0;color:#7a3800';
  var _tdUM='padding:6px 10px;font-size:13px;font-weight:700;text-align:center;white-space:nowrap;color:#c0650a;background:#fff8f0;border-right:1px solid #f5d5b0';
  var uCols=[
    {l:'N (UPM)',v:_uSt.count.toLocaleString(),th:_thU,td:_tdBase},
    {l:'Min UPM%',v:_uSt.min.toFixed(2)+'%',th:_thU,td:_tdBase},
    {l:'Med UPM%',v:_uSt.median.toFixed(2)+'%',th:_thUM,td:_tdUM},
    {l:'Mean UPM%',v:_uSt.mean.toFixed(2)+'%',th:_thU,td:_tdBase},
    {l:'Max UPM%',v:_uSt.max.toFixed(2)+'%',th:_thU,td:_tdBase},
    {l:'SD UPM%',v:_uSt.stddev.toFixed(2)+'%',th:_thU,td:_tdBase}
  ];
  var _hRow=_tbl.querySelector('thead tr'),_dRow=_tbl.querySelector('tbody tr');
  uCols.forEach(function(c){
    if(_hRow){var _th=document.createElement('th');_th.setAttribute('style',c.th);_th.textContent=c.l;_hRow.appendChild(_th);}
    if(_dRow){var _td=document.createElement('td');_td.setAttribute('style',c.td);_td.textContent=c.v;_dRow.appendChild(_td);}
  });
}
window._ptRedrawHistModal=_ptRedrawHistModal;
/* Shared: filter active by checked groups and redraw */
function _ptApplyGroupFilter(){
  var modal=document.getElementById('pt-dist-modal');
  if(!modal)return;
  var rowIdx=modal._ptRowIdx;
  if(rowIdx==null||!_ptRows[rowIdx])return;
  var testName=_ptRows[rowIdx].testName,isCdyn=_ptRows[rowIdx].isCdyn;
  var allActive=modal._ptActive;
  if(!allActive||!allActive.length)return;
  var checkedGroups=new Set();
  var tbl=document.querySelector('#pt-modal-group-stats table');
  var totalRows=tbl?tbl.querySelectorAll('tbody tr').length:0;
  if(tbl){tbl.querySelectorAll('tbody tr').forEach(function(row){
    var cbEl=row.querySelector('input[type=checkbox]');
    var gkEl=row.querySelector('td:nth-child(2)');
    if(cbEl&&gkEl&&cbEl.checked)checkedGroups.add(gkEl.textContent.trim());
  });}
  /* Sync header checkbox state */
  var hCb=tbl&&tbl.querySelector('thead input[type=checkbox]');
  if(hCb){hCb.checked=checkedGroups.size===totalRows;hCb.indeterminate=checkedGroups.size>0&&checkedGroups.size<totalRows;}
  var filteredActive=allActive;
  if(checkedGroups.size<totalRows&&checkedGroups.size>0){
    filteredActive=allActive.filter(function(i){return checkedGroups.has(_ptPopupGroupKey(ROWS[i]));});
    if(!filteredActive.length)filteredActive=allActive;
  }
  if(modal._ptShowUpm){
    /* ⚡ UPM popup */
    if(checkedGroups.size===0){
      var _svg=document.getElementById('pt-modal-upm-svg');
      if(_svg)_svg.innerHTML='<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" font-size="14" fill="#aaa">No groups selected</text>';
      var _note=document.getElementById('pt-modal-upm-note');if(_note)_note.textContent='';
      var _st=document.getElementById('pt-modal-upm-stats');if(_st)_st.innerHTML='';
      return;
    }
    if(typeof drawMiniUpm!=='undefined')drawMiniUpm(filteredActive,testName,isCdyn,'pt-modal-upm-svg','pt-dist-modal-title','pt-modal-upm-note');
    var _uVals=[];
    filteredActive.forEach(function(i){var dp=ROWS[i].die_pairs&&ROWS[i].die_pairs[testName];if(dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))_uVals.push(v);});});
    if(typeof renderStatsTable!=='undefined')renderStatsTable(computeStats(_uVals),'pt-modal-upm-stats',2);
  }else{
    /* 📊 Distribution popup */
    if(checkedGroups.size===0){
      var _svg=document.getElementById('pt-modal-hist-svg');
      if(_svg)_svg.innerHTML='<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" font-size="14" fill="#aaa">No groups selected</text>';
      var _note=document.getElementById('pt-modal-chart-note');if(_note)_note.textContent='';
      var _st=document.getElementById('pt-modal-stats-tbl');if(_st)_st.innerHTML='';
      return;
    }
    _ptRedrawHistModal(filteredActive,testName,isCdyn);
  }
}
window._ptApplyGroupFilter=_ptApplyGroupFilter;
function _ptToggleGroupRow(cb){
  var tr=cb.closest('tr');
  if(!tr)return;
  tr.style.opacity=cb.checked?'1':'0.25';
  tr.querySelectorAll('td:not(:first-child)').forEach(function(c){
    c.style.textDecoration=cb.checked?'none':'line-through';
  });
  _ptApplyGroupFilter();
}
window._ptToggleGroupRow=_ptToggleGroupRow;
function _ptToggleAllGroupRows(cb){
  var tbl=document.querySelector('#pt-modal-group-stats table');
  if(!tbl)return;
  tbl.querySelectorAll('tbody tr').forEach(function(row){
    var cbEl=row.querySelector('input[type=checkbox]');
    if(!cbEl)return;
    cbEl.checked=cb.checked;
    row.style.opacity=cb.checked?'1':'0.25';
    row.querySelectorAll('td:not(:first-child)').forEach(function(c){
      c.style.textDecoration=cb.checked?'none':'line-through';
    });
  });
  _ptApplyGroupFilter();
}
window._ptToggleAllGroupRows=_ptToggleAllGroupRows;
function _ptPopupGroupKey(r){
  var parts=[];
  if(_ptPopupGroupBy.indexOf('program')>=0)parts.push(r.program||'');
  if(_ptPopupGroupBy.indexOf('lot')>=0)parts.push(r.lot||'');
  if(_ptPopupGroupBy.indexOf('wafer')>=0)parts.push(r.wafer||'');
  if(_ptPopupGroupBy.indexOf('material')>=0)parts.push(r.material||'');
  return parts.length?parts.join(' | '):'All';
}
function _ptRenderGroupStats(active,testName,isCdyn,showUpm,containerId){
  var el=document.getElementById(containerId);
  if(!el){return;}
  if(!active.length){el.innerHTML='';return;}
  /* Collect per-group SICC/CDYN values and (for dist mode) per-group UPM values */
  var groupMap={},upmMap={},groupOrder=[];
  var uCol=(!showUpm)?_getUpmCol(testName):null;
  active.forEach(function(i){
    var r=ROWS[i];
    var gk=_ptPopupGroupKey(r);
    if(!groupMap[gk]){groupMap[gk]=[];upmMap[gk]=[];groupOrder.push(gk);}
    var dp=r.die_pairs&&r.die_pairs[testName];
    if(showUpm){
      if(dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))groupMap[gk].push(v);});
    }else{
      if(dp&&dp.s)dp.s.forEach(function(v){if(v!=null&&!isNaN(v)&&v>0)groupMap[gk].push(v);});
      if(uCol&&dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))upmMap[gk].push(v);});
    }
  });
  /* Skip if no data; when groupby is active, also skip if only 1 group (redundant with aggregate table) */
  var nonEmpty=groupOrder.filter(function(g){return groupMap[g].length>0;});
  if(!nonEmpty.length){el.innerHTML='';return;}
  if(_ptPopupGroupBy.length>0&&nonEmpty.length<=1){el.innerHTML='';return;}
  var hasUpm=!showUpm&&uCol&&nonEmpty.some(function(g){return upmMap[g].length>0;});
  var _pal=['#3498db','#27ae60','#e67e22','#9b59b6','#e74c3c','#1abc9c','#f39c12','#2980b9','#c0392b','#16a085'];
  var _th='padding:5px 8px;font-size:10px;font-weight:600;background:#2c3e50;color:#ecf0f1;text-align:center;white-space:nowrap;border-right:1px solid #3d5166';
  var _thHL='padding:5px 8px;font-size:10px;font-weight:700;background:#1a4a7a;color:#fff;text-align:center;white-space:nowrap;border-right:1px solid #3d5166';
  var _thU='padding:5px 8px;font-size:10px;font-weight:600;background:#c0650a;color:#fff;text-align:center;white-space:nowrap;border-right:1px solid #a04000';
  var _thUM='padding:5px 8px;font-size:10px;font-weight:700;background:#9a3412;color:#fff;text-align:center;white-space:nowrap;border-right:1px solid #a04000';
  var dec=showUpm?2:4;
  var fv=function(v){return v!=null?v.toFixed(dec):'--';};
  var label=showUpm?'Per-Group Stats (UPM %)':'Per-Group Stats';
  var upmHdrs=hasUpm
    ?'<th style="'+_thU+'">N (UPM)</th>'
     +'<th style="'+_thU+'">Min UPM%</th>'
     +'<th style="'+_thUM+'">Med UPM%</th>'
     +'<th style="'+_thU+'">Mean UPM%</th>'
     +'<th style="'+_thU+'">Max UPM%</th>'
     +'<th style="'+_thU+';border-right:none">SD UPM%</th>'
    :'';
  var _thCb='padding:5px 4px;font-size:10px;font-weight:600;background:#2c3e50;color:#ecf0f1;text-align:center;white-space:nowrap;border-right:1px solid #3d5166;width:24px';
  var html='<div style="margin-top:10px;font-size:11px;font-weight:bold;color:#2c3e50">'+label+'</div>'
    +'<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;margin-top:4px;font-size:11px">'
    +'<thead><tr>'
    +'<th style="'+_thCb+'"><input type="checkbox" checked title="Select / deselect all" onchange="_ptToggleAllGroupRows(this)" style="cursor:pointer"></th>'
    +'<th style="'+_th+';text-align:left">Group</th>'
    +'<th style="'+_th+'">N (dies)</th>'
    +'<th style="'+_th+'">Min</th>'
    +'<th style="'+_thHL+'">Median</th>'
    +'<th style="'+_th+'">Mean</th>'
    +'<th style="'+_th+'">Max</th>'
    +'<th style="'+_th+(hasUpm?'':';border-right:none')+'">Std Dev</th>'
    +upmHdrs
    +'</tr></thead><tbody>';
  nonEmpty.forEach(function(gk,gi){
    var st=computeStats(groupMap[gk]);
    if(!st)return;
    var clr=_pal[gi%_pal.length];
    var suf=showUpm?'%':'';
    var _uSt=hasUpm?computeStats(upmMap[gk]):null;
    var _tdU='padding:4px 8px;border-bottom:1px solid #eee;text-align:right;color:#7a3800';
    var _tdUM='padding:4px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;color:#c0650a;background:#fff8f0';
    html+='<tr>'
      +'<td style="padding:2px 4px;border-bottom:1px solid #eee;text-align:center"><input type="checkbox" checked title="Hide/show this row" onchange="_ptToggleGroupRow(this)" style="cursor:pointer"></td>'
      +'<td style="padding:4px 8px;border-left:3px solid '+clr+';border-bottom:1px solid #eee;font-weight:bold;white-space:nowrap">'+esc(gk)+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right">'+st.count.toLocaleString()+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right">'+fv(st.min)+suf+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;color:#1a4a7a;background:#eef6ff">'+fv(st.median)+suf+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right">'+fv(st.mean)+suf+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right">'+fv(st.max)+suf+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right'+(hasUpm?'':';border-right:none')+'">'+fv(st.stddev)+suf+'</td>'
      +(_uSt
        ?'<td style="'+_tdU+'">'+_uSt.count.toLocaleString()+'</td>'
         +'<td style="'+_tdU+'">'+_uSt.min.toFixed(2)+'%</td>'
         +'<td style="'+_tdUM+'">'+_uSt.median.toFixed(2)+'%</td>'
         +'<td style="'+_tdU+'">'+_uSt.mean.toFixed(2)+'%</td>'
         +'<td style="'+_tdU+'">'+_uSt.max.toFixed(2)+'%</td>'
         +'<td style="'+_tdU+';border-right:none">'+_uSt.stddev.toFixed(2)+'%</td>'
        :'')
      +'</tr>';
  });
  html+='</tbody></table></div>';
  el.innerHTML=html;
}
window._ptRenderGroupStats=_ptRenderGroupStats;

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
    content.innerHTML=_ptGroupByHTML()
      +'<svg id="pt-modal-upm-svg" style="width:100%;flex:1;min-height:0;display:block;border:1px solid #f5e0c3;border-radius:4px;background:#fffaf4"></svg>'
      +'<div id="pt-modal-upm-note" style="font-size:10px;color:#c0650a;margin-top:3px"></div>'
      +'<div id="pt-modal-upm-stats" style="margin-top:6px"></div>'
      +'<div id="pt-modal-group-stats"></div>';
    modal.style.display='flex';
    modal._ptActive=active;
    if(typeof drawMiniUpm!=='undefined')drawMiniUpm(active,testName,isCdyn,'pt-modal-upm-svg','pt-dist-modal-title','pt-modal-upm-note');
    /* Overwrite basic stats with XY-style renderStatsTable */
    var _allU=[];
    active.forEach(function(i){var dp=ROWS[i].die_pairs&&ROWS[i].die_pairs[testName];if(dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))_allU.push(v);});});
    if(typeof renderStatsTable!=='undefined')renderStatsTable(computeStats(_allU),'pt-modal-upm-stats',2);
    /* Per-group stats */
    _ptRenderGroupStats(active,testName,isCdyn,true,'pt-modal-group-stats');
  }else{
    content.innerHTML=_ptGroupByHTML()
      +'<h3 id="pt-modal-dist-title" style="font-size:12px;color:#2c3e50;margin:0 0 4px"></h3>'
      +'<svg id="pt-modal-hist-svg" style="width:100%;flex:1;min-height:0;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>'
      +'<div id="pt-modal-chart-note" style="font-size:11px;color:#7f8c8d;margin-top:4px"></div>'
      +'<div id="pt-modal-stats-tbl" style="margin-top:8px"></div>'
      +'<div id="pt-modal-group-stats"></div>';
    modal.style.display='flex';
    modal._ptActive=active;
    _ptRedrawHistModal(active,testName,isCdyn);
    /* Per-group stats */
    _ptRenderGroupStats(active,testName,isCdyn,false,'pt-modal-group-stats');
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
