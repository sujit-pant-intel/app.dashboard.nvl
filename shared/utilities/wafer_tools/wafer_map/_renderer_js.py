"""
Single-source JS for wmRender() — SVG wafer map renderer.
See wafermap.md for the full cfg API.

Inject into any HTML page with:
    html = html.replace('</body>', WAFERMAP_JS + '\n</body>', 1)
"""

WAFERMAP_JS = r"""<script>
(function(){'use strict';

// ── shared tooltip div (one per page, reused by all wmRender calls) ───────────
var _wmrTip = null;
// Page-level pinned state — set to the SVG/canvas host that owns the sticky tip,
// or null. Any host checks this before showing a hover tip, so moving to a
// neighbouring tile never overrides a pinned tooltip from another tile.
var _wmrPinnedSvg = null;

function _wmrBuildContent(html, pinned){
  var closeBtn = pinned
    ? '<span data-wmr-close="1" '
      + 'style="cursor:pointer;display:inline-block;background:#c0392b;color:#ffffff;'
      + 'font-size:11px;font-weight:700;padding:2px 9px;border-radius:3px;'
      + 'user-select:none;letter-spacing:.04em;border:1px solid #e74c3c" '
      + 'title="Close tooltip">&#10005;&nbsp;close</span>'
    : '';
  var dragBar = pinned
    ? '<div data-wmr-drag="1" style="display:flex;justify-content:space-between;'
      + 'align-items:center;margin-bottom:5px;cursor:move;padding:2px 0;'
      + 'border-bottom:1px solid #2a4060;user-select:none">'
      + '<span style="font-size:10px;color:#445566;letter-spacing:.05em">&#8597; drag to move</span>'
      + closeBtn
      + '</div>'
    : '';
  return '<div>' + dragBar + '<div>' + html + '</div></div>';
}

// ── drag-to-move for pinned tooltip (wired once, page-level) ────────────────
var _wmrDrag = null;
document.addEventListener('mousedown', function(ev){
  if(!_wmrPinnedSvg) return;
  var tip = _wmrTip;
  if(tip && ev.target && ev.target.getAttribute('data-wmr-drag')){
    var r = tip.getBoundingClientRect();
    _wmrDrag = {ox: ev.clientX - r.left, oy: ev.clientY - r.top};
    ev.preventDefault();
  }
});
document.addEventListener('mousemove', function(ev){
  if(!_wmrDrag) return;
  var tip = _wmrTip;
  tip.style.left = (ev.clientX - _wmrDrag.ox) + 'px';
  tip.style.top  = (ev.clientY - _wmrDrag.oy) + 'px';
});
document.addEventListener('mouseup', function(){
  _wmrDrag = null;
});

function _wmrUnpin(){
  _wmrPinnedSvg = null;
  var tip = _wmrTip;
  if(!tip) return;
  tip.style.display = 'none';
  tip.style.pointerEvents = 'none';
  tip.style.border = '1px solid #2a4060';
  tip.onmousedown = null;
}

function _wmrPin(host, html, x, y){
  _wmrPinnedSvg = host;
  var tip = _wmrEnsureTip();
  tip.innerHTML = _wmrBuildContent(html, true);
  tip.style.display = 'block';
  tip.style.left = (x + 14) + 'px';
  tip.style.top  = (y + 10) + 'px';
  tip.style.pointerEvents = 'auto';
  tip.style.border = '1px solid #4a9fd4';  // blue border = pinned
  tip.onmousedown = function(ev){
    if(ev.target && ev.target.getAttribute('data-wmr-close')){
      _wmrUnpin();
      ev.stopPropagation();
    }
  };
}

function _wmrEnsureTip(){
  if(!_wmrTip){
    _wmrTip = document.createElement('div');
    _wmrTip.style.cssText = (
      'position:fixed;z-index:99990;background:#1a2235;color:#c0ccd8;'
      +'font-family:Arial,sans-serif;font-size:11px;padding:6px 10px;border-radius:5px;'
      +'border:1px solid #2a4060;pointer-events:none;display:none;max-width:320px;'
      +'line-height:1.5;box-shadow:0 2px 12px rgba(0,0,0,0.65)');
    document.body.appendChild(_wmrTip);
  }
  return _wmrTip;
}

// ── contrast color helper ─────────────────────────────────────────────────────
// Returns '#000' or '#fff' for maximum contrast against a hex fill color.
function _wmrContrast(hex){
  if(!hex || hex.charAt(0) !== '#') return '#fff';
  var h = hex.length === 4
    ? hex[1]+hex[1]+hex[2]+hex[2]+hex[3]+hex[3]
    : hex.slice(1);
  var r = parseInt(h.slice(0,2),16)/255;
  var g = parseInt(h.slice(2,4),16)/255;
  var b = parseInt(h.slice(4,6),16)/255;
  // perceived luminance (sRGB)
  var lum = 0.299*r + 0.587*g + 0.114*b;
  return lum > 0.55 ? '#000' : '#fff';
}

// ── wmRender(containerId, cfg) ─────────────────────────────────────────────────
//
// cfg fields (see wafermap.md for the full spec):
//   dies          array of die objects; each MUST have {x, y}; any extra fields
//                 are passed unchanged to colorFn / tooltipFn
//   colorFn       function(die) -> '#rrggbb' fill color; default '#888'
//   tooltipFn     function(die) -> HTML string shown on hover; omit = no tooltip
//   retShots      [[xMin,yMin,xMax,yMax], ...] integer die coords; renderer adds +1 to span
//   retMap        {"x,y": [rdx, rdy, shotIdx]} die->intra-shot position + shot index (optional)
//   retSiteNum    {"rdx,rdy": N} numeric die-loc label (optional)
//   retShotLabels [label, ...] per-shot label text; default 1-based index (optional)
//   width         SVG pixel width; default 280
//   pad           inner padding px; default 2
//   bgColor       SVG background fill; default 'none'
//   borderColor   wafer circle stroke; default '#bdc3c7'
//   shotColor     shot outline stroke; default '#2471a3'
//   shotColors    optional array of per-shot stroke colors (overrides shotColor per index)
//
function wmRender(containerId, cfg){
  var el = (typeof containerId === 'string')
    ? document.getElementById(containerId)
    : containerId;
  if(!el) return;
  el.innerHTML = '';

  cfg = cfg || {};
  var dies        = cfg.dies        || [];
  var colorFn     = cfg.colorFn     || function(){ return '#888'; };
  var tipFn       = cfg.tooltipFn   || null;
  var retShots    = cfg.retShots    || [];
  var retMap      = cfg.retMap      || {};      // "x,y" -> [lx, ly, shotIdx]
  var siteNum     = cfg.retSiteNum  || {};      // "lx,ly" -> N
  var shotLabels  = cfg.retShotLabels === false ? null : (cfg.retShotLabels || []);  // null = suppress all labels
  var W           = cfg.width       || 280;
  var pad         = cfg.pad         || 2;
  var bgColor     = cfg.bgColor     || 'none';
  var borderColor = cfg.borderColor || '#bdc3c7';
  var shotColor   = cfg.shotColor   || '#2471a3';
  var shotColors  = cfg.shotColors  || null;   // optional per-shot color array
  var shotStrokeW = cfg.shotStrokeWidth != null ? cfg.shotStrokeWidth : 1.5;
  var mode        = cfg.mode === 'canvas' ? 'canvas' : 'svg';  // 'canvas' = fast bitmap mode

  if(!dies.length){
    el.innerHTML = '<span style="color:#666;font-size:12px">no data</span>';
    return;
  }

  // ── geometry (shared by both render modes) ─────────────────────────────────
  var xs = dies.map(function(d){ return d.x; });
  var ys = dies.map(function(d){ return d.y; });
  var xMin = Math.min.apply(null, xs), xMax = Math.max.apply(null, xs);
  var yMin = Math.min.apply(null, ys), yMax = Math.max.apply(null, ys);

  // cell size: fit width, maintain aspect ratio
  var cs   = Math.max(2, (W - pad*2) / (xMax - xMin + 1));
  var xSpan = xMax - xMin, ySpan = yMax - yMin;
  var csy  = (xSpan > 0 && ySpan > 0) ? (cs * xSpan / ySpan) : cs;
  var H    = Math.round((yMax - yMin + 1) * csy + pad*2);

  var xCtr = (xMin + xMax) / 2, yCtr = (yMin + yMax) / 2;
  var xRad = (xMax - xMin) / 2 || 1;
  var yRad = (yMax - yMin) / 2 || 1;

  // wafer ellipse params
  var uid = 'wmr_' + (Math.random().toString(36).slice(2));
  var cx  = (pad + (xCtr - xMin)*cs  + cs*0.45).toFixed(1);
  var cy  = (pad + (yMax - yCtr)*csy + csy*0.45).toFixed(1);
  var rx  = (xRad*cs  + cs*0.5).toFixed(1);
  var ry  = (yRad*csy + csy*0.5).toFixed(1);

  if(mode === 'canvas'){
    _wmRenderCanvas(el, W, H, pad, bgColor, borderColor, shotColor, shotColors, shotStrokeW,
      dies, colorFn, tipFn, retShots, shotLabels, xMin, yMax, cs, csy, cx, cy, rx, ry);
    return;
  }

  // ── build SVG markup ────────────────────────────────────────────────────────
  var parts = [];
  parts.push('<defs><clipPath id="'+uid+'">');
  parts.push('<ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry+'"/>');
  parts.push('</clipPath>');
  if(cfg.extraDefs){ parts.push(cfg.extraDefs); }
  parts.push('</defs>');

  if(bgColor && bgColor !== 'none'){
    parts.push('<rect x="0" y="0" width="'+W+'" height="'+H+'" fill="'+bgColor+'"/>');
  }

  parts.push('<g clip-path="url(#'+uid+')">');

  // die rectangles + die-loc number overlays
  var tipMap = {};
  var hasTips = false;
  dies.forEach(function(d, i){
    var px = (pad + (d.x - xMin)*cs).toFixed(1);
    var py = (pad + (yMax - d.y)*csy).toFixed(1);
    var fill = colorFn(d) || '#888';

    var attrs = 'x="'+px+'" y="'+py
      +'" width="'+(cs*0.9).toFixed(1)
      +'" height="'+(csy*0.9).toFixed(1)
      +'" fill="'+fill+'"'
      +' data-x="'+d.x+'" data-y="'+d.y+'"';

    if(tipFn){
      attrs += ' data-i="'+i+'"';
      tipMap[i] = tipFn(d);
      hasTips = true;
    }
    parts.push('<rect '+attrs+'/>');

    // die-loc number (centre of die square)
    var rmEntry = retMap[d.x+','+d.y];
    if(rmEntry){
      var sk  = rmEntry[0]+','+rmEntry[1];
      var num = siteNum[sk] != null ? String(siteNum[sk]) : '';
      if(num){
        var tagFs = Math.max(5, Math.min(10, Math.round(cs*0.44)));
        var tx = (parseFloat(px) + cs*0.45).toFixed(1);
        var ty = (parseFloat(py) + csy*0.5 + tagFs*0.35).toFixed(1);
        var tc = _wmrContrast(fill);
        parts.push('<text x="'+tx+'" y="'+ty
          +'" text-anchor="middle" font-size="'+tagFs
          +'" fill="'+tc+'" stroke="'+(_wmrContrast(fill)==='#fff'?'#0a0f1a':'#f5faff')+'" stroke-width="0.8" paint-order="stroke" font-weight="bold" pointer-events="none">'+num+'</text>');
      }
    }
  });

  // reticle shot outlines (clipped to wafer)
  // retShots entries are [xMin, yMin, xMax, yMax] as integer die coordinates;
  // +1 is added to the span so the outline extends to the outer edge of the last die cell.
  retShots.forEach(function(s, si){
    var sx = (pad + (s[0] - xMin)*cs).toFixed(1);
    var sy = (pad + (yMax - s[3])*csy).toFixed(1);
    var sw = ((s[2] - s[0] + 1)*cs).toFixed(1);
    var sh = ((s[3] - s[1] + 1)*csy).toFixed(1);
    var sc = (shotColors && shotColors[si] != null) ? shotColors[si] : shotColor;
    var sw2 = (shotColors && shotColors[si] != null) ? shotStrokeW * 2.5 : shotStrokeW;
    parts.push('<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh
      +'" fill="none" stroke="'+sc+'" stroke-width="'+sw2+'" opacity="0.92"/>');
  });

  parts.push('</g>');

  // wafer boundary circle (drawn outside clip so it's always visible)
  parts.push('<ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry
    +'" fill="none" stroke="'+borderColor+'" stroke-width="1.5"/>');

  // shot labels — top-left corner of each shot outline (unclipped); pass retShotLabels:false to suppress
  if(shotLabels !== null){
    retShots.forEach(function(s, si){
      var sx = pad + (s[0] - xMin)*cs;
      var sy = pad + (yMax - s[3])*csy;
      var sh = (s[3] - s[1] + 1)*csy;
      var lbl = shotLabels[si] != null ? String(shotLabels[si]) : String(si + 1);
      var lfs = Math.max(6, Math.min(10, Math.round(sh * 0.18)));
      var tx = (sx + 2).toFixed(1);
      var ty = (sy + lfs).toFixed(1);
      parts.push('<text x="'+tx+'" y="'+ty
        +'" text-anchor="start" font-size="'+lfs+'" fill="'+shotColor
        +'" pointer-events="none"'
        +' stroke="#0a0f1a" stroke-width="2" paint-order="stroke">'+lbl+'</text>');
    });
  }
  // ── create SVG element ──────────────────────────────────────────────────────
  var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width',  W);
  svg.setAttribute('height', H);
  svg.style.display = 'block';
  svg.innerHTML = parts.join('');
  el.appendChild(svg);

  // ── tooltip wiring (mouse delegation on the SVG element) ───────────────────
  if(hasTips){
    var tip = _wmrEnsureTip();

    svg.addEventListener('mousemove', function(ev){
      if(_wmrPinnedSvg) return;  // page-level: any pinned tip blocks all hover
      var t = ev.target;
      if(t.tagName === 'rect' && t.hasAttribute('data-i')){
        var html = tipMap[+t.getAttribute('data-i')];
        if(html != null){
          tip.innerHTML = _wmrBuildContent(html, false);
          tip.style.display = 'block';
          tip.style.left = (ev.clientX + 14) + 'px';
          tip.style.top  = (ev.clientY + 10) + 'px';
          tip.style.pointerEvents = 'none';
          tip.style.border = '1px solid #2a4060';
        }
      } else {
        tip.style.display = 'none';
      }
    });

    svg.addEventListener('mouseleave', function(){
      if(_wmrPinnedSvg) return;
      tip.style.display = 'none';
    });

    svg.addEventListener('click', function(ev){
      var t = ev.target;
      if(t.tagName !== 'rect' || !t.hasAttribute('data-i')) return;
      // clicking the same die while pinned → unpin
      if(_wmrPinnedSvg === svg){
        var html = tipMap[+t.getAttribute('data-i')];
        if(html != null) _wmrPin(svg, html, ev.clientX, ev.clientY);
        return;
      }
      // unpin any other SVG first, then pin this one
      _wmrUnpin();
      var html = tipMap[+t.getAttribute('data-i')];
      if(html != null) _wmrPin(svg, html, ev.clientX, ev.clientY);
    });
  }
}

// ── fast canvas render path ──────────────────────────────────────────────────
// Draws the same wafer map as a single <canvas> bitmap instead of one SVG <rect>
// per die. Much faster to build/paint when rendering many wafers at once (grid
// view with dozens/hundreds of thumbnails). Hit-testing for tooltips is done by
// inverse-transforming the mouse position back to a die (x,y) instead of relying
// on per-element DOM targets.
function _wmRenderCanvas(el, W, H, pad, bgColor, borderColor, shotColor, shotColors, shotStrokeW,
    dies, colorFn, tipFn, retShots, shotLabels, xMin, yMax, cs, csy, cx, cy, rx, ry){
  var canvas = document.createElement('canvas');
  var dpr = window.devicePixelRatio || 1;
  canvas.width  = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  canvas.style.width  = W + 'px';
  canvas.style.height = H + 'px';
  canvas.style.display = 'block';
  el.appendChild(canvas);
  var ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  if(bgColor && bgColor !== 'none'){
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, W, H);
  }

  ctx.save();
  ctx.beginPath();
  ctx.ellipse(parseFloat(cx), parseFloat(cy), parseFloat(rx), parseFloat(ry), 0, 0, Math.PI*2);
  ctx.clip();

  // die -> lookup index, for hover/click hit-testing via inverse coordinate transform
  var dieMap = {};
  // Group by fill color first so ctx.fillStyle is set once per color instead of
  // once per die — cuts canvas state changes from N dies to a handful of colors.
  var byColor = {};
  dies.forEach(function(d, i){
    dieMap[d.x+','+d.y] = i;
    var fill = colorFn(d) || '#888';
    (byColor[fill] || (byColor[fill] = [])).push(d);
  });
  Object.keys(byColor).forEach(function(fill){
    ctx.fillStyle = fill;
    byColor[fill].forEach(function(d){
      var px = pad + (d.x - xMin)*cs;
      var py = pad + (yMax - d.y)*csy;
      ctx.fillRect(px, py, cs*0.9, csy*0.9);
    });
  });

  retShots.forEach(function(s, si){
    var sx = pad + (s[0] - xMin)*cs;
    var sy = pad + (yMax - s[3])*csy;
    var sw = (s[2] - s[0] + 1)*cs;
    var sh = (s[3] - s[1] + 1)*csy;
    ctx.strokeStyle = (shotColors && shotColors[si] != null) ? shotColors[si] : shotColor;
    ctx.lineWidth = (shotColors && shotColors[si] != null) ? shotStrokeW * 2.5 : shotStrokeW;
    ctx.globalAlpha = 0.92;
    ctx.strokeRect(sx, sy, sw, sh);
    ctx.globalAlpha = 1;
  });

  ctx.restore();

  ctx.strokeStyle = borderColor;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.ellipse(parseFloat(cx), parseFloat(cy), parseFloat(rx), parseFloat(ry), 0, 0, Math.PI*2);
  ctx.stroke();

  if(shotLabels !== null){
    ctx.textBaseline = 'top';
    retShots.forEach(function(s, si){
      var sx = pad + (s[0] - xMin)*cs;
      var sy = pad + (yMax - s[3])*csy;
      var sh = (s[3] - s[1] + 1)*csy;
      var lbl = shotLabels[si] != null ? String(shotLabels[si]) : String(si + 1);
      var lfs = Math.max(6, Math.min(10, Math.round(sh * 0.18)));
      ctx.font = lfs + 'px Arial,sans-serif';
      ctx.strokeStyle = '#0a0f1a';
      ctx.lineWidth = 2;
      ctx.strokeText(lbl, sx + 2, sy);
      ctx.fillStyle = shotColor;
      ctx.fillText(lbl, sx + 2, sy);
    });
  }

  if(!tipFn) return;

  var tip = _wmrEnsureTip();

  function _dieAt(ev){
    var r = canvas.getBoundingClientRect();
    var mx = ev.clientX - r.left, my = ev.clientY - r.top;
    var dx = Math.round((mx - pad) / cs + xMin);
    var dy = yMax - Math.round((my - pad) / csy);
    var i = dieMap[dx+','+dy];
    return i != null ? dies[i] : null;
  }

  canvas.addEventListener('mousemove', function(ev){
    if(_wmrPinnedSvg) return;
    var d = _dieAt(ev);
    if(d){
      tip.innerHTML = _wmrBuildContent(tipFn(d), false);
      tip.style.display = 'block';
      tip.style.left = (ev.clientX + 14) + 'px';
      tip.style.top  = (ev.clientY + 10) + 'px';
      tip.style.pointerEvents = 'none';
      tip.style.border = '1px solid #2a4060';
    } else {
      tip.style.display = 'none';
    }
  });

  canvas.addEventListener('mouseleave', function(){
    if(_wmrPinnedSvg) return;
    tip.style.display = 'none';
  });

  canvas.addEventListener('click', function(ev){
    var d = _dieAt(ev);
    if(!d) return;
    if(_wmrPinnedSvg === canvas){
      _wmrPin(canvas, tipFn(d), ev.clientX, ev.clientY);
      return;
    }
    _wmrUnpin();
    _wmrPin(canvas, tipFn(d), ev.clientX, ev.clientY);
  });
}

window.wmRender = wmRender;

})();
</script>"""
