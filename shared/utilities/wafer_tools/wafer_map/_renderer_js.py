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
// Page-level pinned state — set to the SVG that owns the sticky tip, or null.
// Any SVG checks this before showing a hover tip, so moving to a neighbouring
// tile never overrides a pinned tooltip from another tile.
var _wmrPinnedSvg = null;

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
  var shotStrokeW = cfg.shotStrokeWidth != null ? cfg.shotStrokeWidth : 1.5;

  if(!dies.length){
    el.innerHTML = '<span style="color:#666;font-size:12px">no data</span>';
    return;
  }

  // ── geometry ────────────────────────────────────────────────────────────────
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
    parts.push('<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh
      +'" fill="none" stroke="'+shotColor+'" stroke-width="'+shotStrokeW+'" opacity="0.85"/>');
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

    // ── drag-to-move for pinned tooltip ──────────────────────────────────────
    var _wmrDrag = null;  // {dx, dy} offset from tip origin to mousedown point
    document.addEventListener('mousedown', function(ev){
      if(!_wmrPinnedSvg) return;
      if(ev.target && ev.target.getAttribute('data-wmr-drag')){
        var r = tip.getBoundingClientRect();
        _wmrDrag = {ox: ev.clientX - r.left, oy: ev.clientY - r.top};
        ev.preventDefault();
      }
    });
    document.addEventListener('mousemove', function(ev){
      if(!_wmrDrag) return;
      tip.style.left = (ev.clientX - _wmrDrag.ox) + 'px';
      tip.style.top  = (ev.clientY - _wmrDrag.oy) + 'px';
    });
    document.addEventListener('mouseup', function(){
      _wmrDrag = null;
    });

    function _wmrUnpin(){
      _wmrPinnedSvg = null;
      tip.style.display = 'none';
      tip.style.pointerEvents = 'none';
      tip.style.border = '1px solid #2a4060';
      tip.onmousedown = null;
    }

    function _wmrPin(html, x, y){
      _wmrPinnedSvg = svg;
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
        if(html != null) _wmrPin(html, ev.clientX, ev.clientY);
        return;
      }
      // unpin any other SVG first, then pin this one
      _wmrUnpin();
      var html = tipMap[+t.getAttribute('data-i')];
      if(html != null) _wmrPin(html, ev.clientX, ev.clientY);
    });
  }
}

window.wmRender = wmRender;

})();
</script>"""
