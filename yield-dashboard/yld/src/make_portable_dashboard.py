#!/usr/bin/env python3
"""
make_portable_dashboard.py
--------------------------
Creates a self-contained portable copy of Dashboard.html.

Strategy per link type
  * Relative HTML hrefs  → Blob URL (JS runs properly)
  * file:// local HTML   → Blob URL (resolved to local path)
  * Relative images/CSS  → base64 data URI
  * iframe src (html)    → srcdoc  (initial load only)
  * load('x.html') JS   → pre-embedded blob map + load() override
  * xlsx / .jmp / .jmpprj / http://127.0.0.1 → disabled button

Usage:
    python src/make_portable_dashboard.py <Dashboard.html> [--out <output.html>]
"""

import argparse
import base64
import html as html_mod
import json
import mimetypes
import os
import re
import sys
from pathlib import Path


_MIME_DEFAULTS = {
    '.html': 'text/html',
    '.htm':  'text/html',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.svg':  'image/svg+xml',
    '.css':  'text/css',
    '.js':   'application/javascript',
    '.ico':  'image/x-icon',
    '.webp': 'image/webp',
}

# Extensions that should be disabled (can't be embedded usefully)
_DISABLE_EXTS = {'.xlsx', '.xls', '.jmp', '.jmpprj', '.csv', '.zip', '.sas7bdat'}

# Regex that matches a full <a ...> tag including > chars inside quoted attributes
# e.g. onclick="catch(()=>...)"  — [^>]+ would break on the =>)
_ANCHOR_RE = re.compile(
    r'<a\b(?:[^>"\']*("[^"]*"|\'[^\']*\'))*[^>]*>',
    re.DOTALL)


def _mime(path: Path) -> str:
    return _MIME_DEFAULTS.get(
        path.suffix.lower(),
        mimetypes.guess_type(str(path))[0] or 'application/octet-stream',
    )


def _to_data_uri(path: Path, mime: str = None) -> str:
    mime = mime or _mime(path)
    data = base64.b64encode(path.read_bytes()).decode('ascii')
    return f'data:{mime};base64,{data}'


def _is_external(url: str) -> bool:
    return (url.startswith('data:')
            or url.startswith('http:')
            or url.startswith('https:')
            or url.startswith('//')
            or url.startswith('file://')
            or url.startswith('#')
            or url.startswith('javascript:')
            or url.startswith('mailto:'))


def _resolve_file_url(url: str) -> Path | None:
    """Convert file:// URL to a local Path if it exists.
    Handles both file:///local/path and file://server/unc/path forms."""
    try:
        from urllib.parse import unquote
        if url.startswith('file:///'):
            # Local path: file:///C:/... or file:///path/...
            from urllib.request import url2pathname
            path_str = url2pathname(url[7:])  # strip "file://"
            p = Path(path_str)
            return p if p.is_file() else None
        elif url.startswith('file://'):
            # UNC path: file://server/share/path → \\server\share\path
            rest = unquote(url[7:])   # strip "file://"
            unc  = '\\\\' + rest.replace('/', '\\')
            p = Path(unc)
            return p if p.is_file() else None
    except Exception:
        pass
    return None


def _html_to_b64(html_str: str) -> str:
    return base64.b64encode(html_str.encode('utf-8')).decode('ascii')


# ---------------------------------------------------------------------------
# Embed static resources (images, CSS) inside an HTML string.
# HTML references are NOT touched here — handled elsewhere.
# ---------------------------------------------------------------------------
def _embed_static(html: str, base: Path, depth: int = 0) -> str:
    """Embed img/script src (non-HTML) and inline CSS link tags."""
    if depth > 8:
        return html

    def _replace_src(m):
        src = m.group(1)
        if _is_external(src):
            return m.group(0)
        if src.lower().split('?')[0].endswith(('.html', '.htm')):
            return m.group(0)  # handled separately
        try:
            p = (base / src).resolve()
            if p.is_file():
                return f'src="{_to_data_uri(p)}"'
        except Exception:
            pass
        return m.group(0)

    html = re.sub(r'\bsrc="([^"]+)"', _replace_src, html)

    def _replace_link(m):
        href = m.group(1)
        if _is_external(href):
            return m.group(0)
        try:
            p = (base / href).resolve()
            if p.is_file() and p.suffix.lower() == '.css':
                css = p.read_text(encoding='utf-8', errors='replace')
                return f'<style>{css}</style>'
        except Exception:
            pass
        return m.group(0)

    html = re.sub(
        r'<link\b[^>]*\brel=["\']stylesheet["\'][^>]*\bhref="([^"]+)"[^>]*/?>',
        _replace_link, html, flags=re.IGNORECASE)

    return html


# ---------------------------------------------------------------------------
# Disable non-embeddable links (xlsx, jmp, local server)
# ---------------------------------------------------------------------------
_DISABLED_STYLE = ('style="opacity:0.4;pointer-events:none;cursor:default;'
                   'text-decoration:line-through" title="Not available in portable version"')


def _check_anchor(m):
    full = m.group(0)
    # extract href value
    hm = re.search(r'\bhref="([^"]+)"', full)
    if not hm:
        return full
    href = hm.group(1)
    # local opener server (127.0.0.1) — always disable
    if re.search(r'127\.0\.0\.1:\d+/open', href):
        return _apply_disabled(full)
    # file:// — both file:/// (local) and file://server/ (UNC)
    if href.startswith('file://'):
        ext = Path(href.split('?')[0].split('/')[-1]).suffix.lower()
        if ext in _DISABLE_EXTS:
            return _apply_disabled(full)
    if not _is_external(href):
        ext = Path(href.split('?')[0]).suffix.lower()
        if ext in _DISABLE_EXTS:
            return _apply_disabled(full)
    return full


def _apply_disabled(tag: str) -> str:
    """Inject disabled style and neutralise onclick/href on an <a> tag."""
    tag = re.sub(r'\bhref="[^"]*"', 'href="#"', tag)
    tag = re.sub(r'\bonclick="[^"]*"', '', tag)
    if 'style="' in tag:
        tag = tag.replace('style="', 'style="opacity:0.4;pointer-events:none;cursor:default;text-decoration:line-through;', 1)
    else:
        tag = tag.rstrip('>') + f' {_DISABLED_STYLE}>'
    return tag


def _disable_links(html: str) -> str:
    return _ANCHOR_RE.sub(_check_anchor, html)


# ---------------------------------------------------------------------------
# Embed a sidebar-viewer index.html:
# Pre-embed all load('x.html') targets as blobs, override window.load().
# Also embed iframe initial src as srcdoc.
# ---------------------------------------------------------------------------
def _embed_viewer_html(html: str, base: Path, depth: int) -> str:
    """Fully embed a load()-based sidebar viewer."""
    # Collect all load('...') file references
    load_targets = list(dict.fromkeys(re.findall(r"load\('([^']+\.html?)'", html)))

    # Also collect initial iframe src
    iframe_srcs = re.findall(r'<iframe\b[^>]+\bsrc="([^"]+\.html?)"', html, re.IGNORECASE)

    # Also collect direct href="*.html" anchors with target="content" (e.g. wafer map)
    href_content = re.findall(
        r'href="([^"#][^"]*\.html?)"[^>]*target=["\']content["\']',
        html, re.IGNORECASE)
    href_content += re.findall(
        r'target=["\']content["\'][^>]{0,200}href="([^"#][^"]*\.html?)"',
        html, re.IGNORECASE)

    all_targets = list(dict.fromkeys(load_targets + iframe_srcs + href_content))

    # ── Scan sub-pages for paretoNav / FP_DATA URLs and wmLoad URLs ──────
    # paretoNav targets must be in the PARENT viewer's __pl map because
    # paretoNav calls window.parent.frame.src.
    # wmLoad targets are consumed within the sub-page (local blob map).
    _wm_load_map: dict = {}   # rel → [wm_url, ...]

    for rel in list(all_targets):          # iterate a snapshot; we'll append below
        p = (base / rel)
        if not p.is_file():
            continue
        try:
            sub_raw = p.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue

        # paretoNav('url') — static onclick rows
        # Use pure relative path arithmetic (avoid resolve() to stay on mapped drive)
        for pnt in re.findall(r"paretoNav\('([^']+\.html?)'\)", sub_raw):
            pnt_rel = str(Path(rel).parent / pnt).replace('\\', '/')
            if pnt_rel not in all_targets and (base / pnt_rel).is_file():
                all_targets.append(pnt_rel)

        # "url":"..." keys inside FP_DATA / similar JS JSON blobs
        for fp_url in re.findall(r'"url"\s*:\s*"([^"]+\.html?)"', sub_raw):
            fp_rel = str(Path(rel).parent / fp_url).replace('\\', '/')
            if fp_rel not in all_targets and (base / fp_rel).is_file():
                all_targets.append(fp_rel)

        # WM_FILES={"lot":"heatmap/...html"} — wafer map targets for fbTileClick
        # Also pick up WM_URL="wafermap.html" fallback
        if 'WM_FILES' in sub_raw or 'WM_URL' in sub_raw:
            # WM_URL single fallback
            _wm_url_m = re.search(r'var\s+WM_URL\s*=\s*"([^"]+\.html?)"', sub_raw)
            if _wm_url_m:
                _wu_rel = str(Path(rel).parent / _wm_url_m.group(1)).replace('\\', '/')
                if _wu_rel not in all_targets and (base / _wu_rel).is_file():
                    all_targets.append(_wu_rel)
            # WM_FILES dict values
            _wm_block = re.search(r'var\s+WM_FILES\s*=\s*(\{[^}]*\})', sub_raw)
            if _wm_block:
                for wf_url in re.findall(r'"([^"]+\.html?)"', _wm_block.group(1)):
                    wf_rel = str(Path(rel).parent / wf_url).replace('\\', '/')
                    if wf_rel not in all_targets and (base / wf_rel).is_file():
                        all_targets.append(wf_rel)

        # wmLoad('url') — iframe targets served within the sub-page itself
        wm_urls = list(dict.fromkeys(re.findall(r"wmLoad\('([^']+)'", sub_raw)))
        if wm_urls:
            _wm_load_map[rel] = wm_urls

    # Build base64 map: relative path → b64 of (recursively embedded) HTML
    file_map = {}
    for rel in all_targets:
        p = base / rel
        if p.is_file():
            inner = p.read_text(encoding='utf-8', errors='replace')
            inner = _embed_static(inner, p.parent, depth + 1)
            inner = _disable_links(inner)

            # ── Inject paretoNav override ─────────────────────────────────
            # In portable mode, window.parent.__pl(url) must be used instead
            # of setting window.parent.frame.src (frame uses srcdoc).
            if 'paretoNav' in inner:
                _pn_ovr = (
                    '<script>(function(){'
                    'window.paretoNav=function(url){'
                    'var par=window.parent;'
                    'if(par&&par.__pl){par.__pl(url);return;}'
                    'try{var f=par.document.getElementById(\'frame\');'
                    'if(f){f.src=url;return;}}catch(e){}'
                    'window.open(url,\'_blank\');};'
                    '})();</script>'
                )
                inner = (inner.replace('</head>', _pn_ovr + '</head>', 1)
                         if '</head>' in inner else _pn_ovr + inner)

            # ── Inject fbTileClick portable nav override ──────────────────
            # fbTileClick sets window.parent.frame.src for wafer map navigation.
            # In portable mode, rewrite to use __pl instead.
            if 'fbTileClick' in inner and 'WM_URL' in inner:
                # Patch: replace frame.src assignment with __pl call
                inner = re.sub(
                    r'try\{var f=window\.parent\.document\.getElementById\([\'\"]frame[\'\"]\);if\(f\)\{f\.src=_wmTarget;\}else\{throw 0;\}\}',
                    'try{var par=window.parent;if(par&&par.__pl){par.__pl(_wmTarget);}else{var f=par.document.getElementById(\'frame\');if(f){f.src=_wmTarget;}else{throw 0;}}}',
                    inner
                )

            # ── Inject wmLoad / _wmRender override ───────────────────────
            # _wmRender creates <iframe src=url> for lot/wafer files.
            # Embed those files as a local srcdoc blob map and override
            # _wmRender to use srcdoc= so no on-disk files are needed.
            if rel in _wm_load_map:
                wm_b64: dict = {}
                for wm_url in _wm_load_map[rel]:
                    # Use base/rel-relative path arithmetic, no resolve()
                    wm_abs = base / Path(rel).parent / wm_url
                    if wm_abs.is_file():
                        try:
                            wm_html = wm_abs.read_text(encoding='utf-8', errors='replace')
                            wm_html = _embed_static(wm_html, wm_abs.parent, depth + 2)
                            wm_html = _disable_links(wm_html)
                            wm_b64[wm_url] = _html_to_b64(wm_html)
                        except Exception:
                            pass
                if wm_b64:
                    wm_map_json = json.dumps(wm_b64, separators=(',', ':'))
                    _wm_ovr = (
                        f'<script>(function(){{'
                        f'var _W={wm_map_json};'
                        f'function _dec(b64){{var b=atob(b64),a=new Uint8Array(b.length);'
                        f'for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);'
                        f'return new TextDecoder().decode(a);}}'
                        f'function _wmFind(url){{'
                        f'var u=(url||"").replace(/\\\\/g,"/");'
                        f'var base=u.split("#")[0];'
                        f'var b64=_W[u]||_W[base];'
                        f'if(!b64){{'
                        f'var bn=base.split("/").pop();'
                        f'for(var k in _W){{if(k.split("/").pop()===bn){{b64=_W[k];break;}}}}'
                        f'}}'
                        f'return{{b64:b64,frag:(u.indexOf("#")>=0?u.split("#")[1]:"")}};'
                        f'}}'
                        f'window._wmRender=function(){{'
                        f'var wrap=document.getElementById("wm-frames");'
                        f'if(!wrap)return;wrap.innerHTML="";'
                        f'window._wmSel.forEach(function(row,url){{'
                        f'var f=document.createElement("iframe");'
                        f'var r=_wmFind(url);'
                        f'if(r.b64){{'
                        f'f.srcdoc=_dec(r.b64);'
                        f'if(r.frag){{f.addEventListener("load",function(){{'
                        f'try{{var d=f.contentDocument||f.contentWindow.document;'
                        f'var e=d.getElementById(r.frag);if(e)e.scrollIntoView();}}catch(_e){{}}'
                        f'}});}}'
                        f'}}else{{f.src=url;}}'
                        f'wrap.appendChild(f);}});}};'
                        f'}})();</script>'
                    )
                    inner = (inner.replace('</body>', _wm_ovr + '</body>', 1)
                             if '</body>' in inner else inner + _wm_ovr)

            file_map[rel] = _html_to_b64(inner)
        else:
            print(f'    [skip] not found: {base / rel}')

    if not file_map:
        return html

    # Rewrite href="x.html" target="content" anchors → onclick="__pl('x.html',this)"
    def _rewrite_content_href(m):
        tag = m.group(0)
        href_m = re.search(r'href="([^"]+)"', tag)
        if not href_m:
            return tag
        rel = href_m.group(1)
        if rel not in file_map:
            return tag
        tag = re.sub(r'\bhref="[^"]*"', 'href="#"', tag)
        tag = re.sub(r'\btarget=["\']content["\']', '', tag, flags=re.IGNORECASE)
        tag = re.sub(r'\bonclick="[^"]*"', '', tag)
        tag = tag.rstrip('>')
        tag += f" onclick=\"__pl('{rel}',this);return false;\">"
        return tag
    html = _ANCHOR_RE.sub(_rewrite_content_href, html)

    # Replace iframe src= with srcdoc= for the initial load
    def _iframe_srcdoc(m):
        src = m.group(1)
        if src in file_map:
            inner_html = base64.b64decode(file_map[src]).decode('utf-8')
            escaped = html_mod.escape(inner_html, quote=True)
            tag = m.group(0)
            tag = re.sub(r'\bsrc="[^"]*"', f'srcdoc="{escaped}"', tag)
            return tag
        return m.group(0)

    html = re.sub(r'<iframe\b([^>]*)\bsrc="([^"]+\.html?)"([^>]*)>',
                  lambda m: _iframe_srcdoc_full(m, file_map), html, flags=re.IGNORECASE)

    # Rewrite onclick="load('x.html', this)" → onclick="__pl('x.html', this)"
    # This is necessary because function declarations are hoisted and window.load
    # assignments cannot override them; rewriting the call site is the only fix.
    html = re.sub(r"""onclick="load\(""", 'onclick="__pl(', html)

    # Inject JS blob map + portable __pl loader before </body>.
    # Use frame.srcdoc (UTF-8 decoded string) instead of frame.src = blob URL,
    # because URL.createObjectURL is unreliable inside a blob-URL document context.
    map_json = json.dumps(file_map, separators=(',', ':'))
    override_js = f"""
<script id="_portable_viewer">(function(){{
  var _map={map_json};
  function _decode(b64){{
    // Decode base64 → Uint8Array → UTF-8 string (handles non-ASCII)
    var bin=atob(b64),a=new Uint8Array(bin.length);
    for(var i=0;i<bin.length;i++)a[i]=bin.charCodeAt(i);
    return new TextDecoder().decode(a);
  }}
  // __pl: portable load — replaces hoisted function load() via onclick rewrite
  window.__pl=function(url,el){{
    var frame=document.getElementById('frame');
    if(!frame)return;
    var frag='';
    var hashIdx=url.indexOf('#');
    if(hashIdx>=0){{frag=url.substring(hashIdx+1);url=url.substring(0,hashIdx);}}
    var b64=_map[url];
    if(!b64){{
      // try basename match for paths like heatmap/x.html
      var base=url.split('/').pop();
      for(var k in _map){{
        if(k.split('/').pop()===base){{b64=_map[k];break;}}
      }}
    }}
    if(b64){{
      frame.srcdoc=_decode(b64);
      if(frag){{frame.addEventListener('load',function _scrollFrag(){{
        frame.removeEventListener('load',_scrollFrag);
        try{{var d=frame.contentDocument||frame.contentWindow.document;
        var e=d.getElementById(frag);if(e)e.scrollIntoView();}}catch(_e){{}}
      }});}}
    }}
    if(el){{
      document.querySelectorAll('a.nav-link,a.sub-link,a.subsub-link').forEach(function(l){{l.classList.remove('active');}});
      el.classList.add('active');
    }}
  }};
}})();
</script>"""

    if '</body>' in html:
        html = html.replace('</body>', override_js + '\n</body>', 1)
    else:
        html += override_js

    return html


def _iframe_srcdoc_full(m, file_map):
    # group(0) = full tag, need to find src attr
    tag = m.group(0)
    src_m = re.search(r'\bsrc="([^"]+)"', tag)
    if not src_m:
        return tag
    src = src_m.group(1)
    if src in file_map:
        inner_html = base64.b64decode(file_map[src]).decode('utf-8')
        escaped = html_mod.escape(inner_html, quote=True)
        # Use lambda so escaped content is not interpreted as a regex replacement
        # (Python 3.12+ raises re.error on bare \s, \d, etc. in repl strings)
        tag = re.sub(r'\bsrc="[^"]*"', lambda _: f'srcdoc="{escaped}"', tag)
    return tag


# ---------------------------------------------------------------------------
# Top-level: detect if an HTML file is a viewer and apply appropriate embed
# ---------------------------------------------------------------------------
def _is_viewer_html(html: str) -> bool:
    """True if this HTML uses load() to swap iframe content."""
    return bool(re.search(r"window\.load\s*=|function\s+load\s*\(", html)
                or re.search(r"document\.getElementById\(['\"]frame['\"]\)\.src", html))


def _embed_sub_html_hrefs(html: str, base: Path, depth: int) -> str:
    """Embed relative and file:// HTML hrefs inside an already-embedded page
       as _openPortable blobs (new-tab opener). Only goes 3 levels deep."""
    if depth > 3:
        return html
    sub_map = {}
    counter = [0]

    def _sub(m):
        full_tag = m.group(0)
        hm = re.search(r'\bhref="([^"]+)"', full_tag)
        if not hm:
            return full_tag

        # Keep in-viewer navigation anchors intact; _embed_viewer_html handles
        # load('...') targets and rewrites them to __pl() for srcdoc rendering.
        if re.search(r'\bonclick="[^"]*\bload\(', full_tag, re.IGNORECASE):
            return full_tag

        href = hm.group(1)
        if href.startswith('#') or href.startswith('javascript:') or not href:
            return full_tag

        local_path = None
        if href.startswith('file://'):
            ext = Path(href.split('?')[0].split('/')[-1]).suffix.lower()
            if ext in _DISABLE_EXTS:
                return full_tag  # _disable_links will handle
            local_path = _resolve_file_url(href)
        elif _is_external(href):
            return full_tag
        else:
            ext = Path(href.split('?')[0]).suffix.lower()
            if ext in _DISABLE_EXTS:
                return full_tag  # _disable_links will handle
            if ext not in ('.html', '.htm'):
                return full_tag  # _embed_static handles non-HTML
            local_path = base / href.split('?')[0]

        if local_path is None or not local_path.is_file():
            return full_tag

        try:
            inner = local_path.read_text(encoding='utf-8', errors='replace')
            inner = _embed_html_file(inner, local_path.parent, depth + 1)
            key = f'_s{counter[0]}'
            counter[0] += 1
            sub_map[key] = _html_to_b64(inner)
            new_tag = re.sub(r'\bhref="[^"]*"', 'href="#"', full_tag)
            new_tag = re.sub(r'\btarget="[^"]*"', '', new_tag)
            new_tag = re.sub(r'\bonclick="(?:[^"]*|(?:"[^"]*"))*"', '', new_tag)
            new_tag = new_tag.rstrip('>')
            new_tag += f' onclick="event.preventDefault();window._openPortable(\'{key}\')">'
            return new_tag
        except Exception as e:
            print(f'  Warning: could not sub-embed {href}: {e}')
            return full_tag

    html = _ANCHOR_RE.sub(_sub, html)

    if sub_map:
        map_json = json.dumps(sub_map, separators=(',', ':'))
        inject = (f'<script>(function(){{var m={map_json};'
                  f'var p=window._openPortable;'
                  f'window._openPortable=function(k){{'
                  f'if(m[k]){{var b=atob(m[k]),a=new Uint8Array(b.length);'
                  f'for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);'
                  f'window.open(URL.createObjectURL(new Blob([a],{{type:\'text/html\'}})),\'_blank\');}}'
                  f'else if(p)p(k);}};}})()</script>')
        html = html.replace('</body>', inject + '</body>', 1) if '</body>' in html else html + inject

    return html


def _embed_html_file(html: str, base: Path, depth: int = 0) -> str:
    """Fully embed a single HTML file: static resources + viewer logic if applicable."""
    html = _embed_static(html, base, depth)
    html = _embed_sub_html_hrefs(html, base, depth)  # embed relative HTML links
    html = _disable_links(html)
    if _is_viewer_html(html):
        print(f'    [viewer] {base.name}')
        html = _embed_viewer_html(html, base, depth)
    return html


# ---------------------------------------------------------------------------
# JS blob script injected into the top-level portable file
# ---------------------------------------------------------------------------
_TOP_BLOB_SCRIPT = """
<script id="_portable_blobs">(function(){{
  var _blobs={blob_map};
  window._openPortable=function(key){{
    var b64=_blobs[key];
    if(!b64)return;
    var b=atob(b64),a=new Uint8Array(b.length);
    for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);
    window.open(URL.createObjectURL(new Blob([a],{{type:'text/html'}})),'_blank');
  }};
}})();
</script>
"""


def _unc_to_drive(path: Path) -> Path:
    """On Windows, try to convert a UNC path (\\\\server\\share\\...) to the
    equivalent mapped drive letter path (X:\\...).  If no mapping is found or the
    platform is not Windows, the original path is returned unchanged.

    This is needed because tkinter's file dialog can return UNC paths when the user
    navigates to a share without using its drive-letter mapping.  UNC paths work for
    simple file access but cause problems when Path arithmetic produces very long paths
    (>MAX_PATH) or when SMB latency causes is_file() checks to time out.
    """
    s = str(path)
    if not s.startswith('\\\\'):
        return path
    try:
        import subprocess as _sp
        r = _sp.run(
            ['net', 'use'], capture_output=True, text=True, timeout=5,
            creationflags=0x08000000)          # CREATE_NO_WINDOW
        for line in r.stdout.splitlines():
            # Typical net-use line:  OK    M:    \\server\share    ...
            cols = line.split()
            if len(cols) >= 3 and len(cols[1]) == 2 and cols[1][1] == ':' \
                    and cols[2].startswith('\\\\'):
                drive    = cols[1].upper()            # e.g. "M:"
                unc_root = cols[2].rstrip('\\')       # e.g. "\\server\share"
                if s.lower().startswith(unc_root.lower()):
                    remainder = s[len(unc_root):]
                    return Path(drive + '\\' + remainder.lstrip('\\'))
    except Exception:
        pass
    return path


def make_portable(dashboard_path: Path, output_path: Path = None) -> Path:
    # Normalize UNC paths (\\server\share\...) → mapped drive (X:\...) so that
    # all subsequent Path arithmetic stays on the fast, short drive-letter form.
    dashboard_path = _unc_to_drive(dashboard_path)

    if output_path is None:
        output_path = dashboard_path.parent / 'Dashboard_portable.html'

    print(f'Reading  {dashboard_path}')
    html = dashboard_path.read_text(encoding='utf-8', errors='replace')
    base = dashboard_path.parent

    print('Embedding resources …')
    blob_map = {}
    blob_counter = [0]

    def _collect_href(m):
        full_tag = m.group(0)
        href_m = re.search(r'\bhref="([^"]+)"', full_tag)
        if not href_m:
            return full_tag
        href = href_m.group(1)

        # Leave target="content" links for _embed_viewer_html to wire into __pl
        if re.search(r'\btarget=["\']content["\']', full_tag, re.IGNORECASE):
            return full_tag

        # Resolve file:// local HTML (both file:/// local and file://server/ UNC)
        local_path = None
        if href.startswith('file://'):
            # Check extension first — disable non-embeddable file:// links
            _fe = Path(href.split('?')[0].split('/')[-1]).suffix.lower()
            if _fe in _DISABLE_EXTS:
                return _apply_disabled(full_tag)
            local_path = _resolve_file_url(href)
            if local_path is None or local_path.suffix.lower() not in ('.html', '.htm'):
                return _apply_disabled(full_tag)  # unresolvable file:// link
        elif _is_external(href):
            return full_tag
        else:
            clean = href.split('?')[0].split('#')[0]
            if not clean:
                return full_tag
            ext = Path(clean).suffix.lower()
            if ext in _DISABLE_EXTS:
                return _apply_disabled(full_tag)
            local_path = base / clean
            if not local_path.is_file():
                return full_tag
            if local_path.suffix.lower() not in ('.html', '.htm'):
                return re.sub(r'\bhref="[^"]*"', f'href="{_to_data_uri(local_path)}"', full_tag)

        # Embed as blob
        try:
            inner = local_path.read_text(encoding='utf-8', errors='replace')
            print(f'  Embedding {local_path.name} …')
            inner = _embed_html_file(inner, local_path.parent, depth=1)
            encoded = _html_to_b64(inner)
            key = f'h{blob_counter[0]}'
            blob_counter[0] += 1
            blob_map[key] = encoded
            new_tag = re.sub(r'\bhref="[^"]*"', 'href="#"', full_tag)
            new_tag = re.sub(r'\bonclick="[^"]*"', '', new_tag)
            new_tag = new_tag.rstrip('>')
            new_tag += f' onclick="event.preventDefault();window._openPortable(\'{key}\')">'
            return new_tag
        except Exception as e:
            print(f'  Warning: could not embed {href}: {e}')
        return full_tag

    html = _ANCHOR_RE.sub(_collect_href, html)

    # Embed static resources in top-level HTML itself
    html = _embed_static(html, base, depth=0)

    # If the top-level file is a sidebar viewer (uses load() navigation),
    # embed all load() targets AND href="*.html" target="content" links via __pl.
    # This must run AFTER _collect_href so any remaining direct hrefs are already
    # handled, and AFTER _embed_static so images inside sub-pages are inlined.
    if _is_viewer_html(html):
        print('  [viewer] Embedding sidebar navigation targets ...')
        html = _embed_viewer_html(html, base, depth=0)

    html = _disable_links(html)

    # Inject top-level blob JS before </body>
    blob_json = json.dumps(blob_map, separators=(',', ':'))
    blob_script = _TOP_BLOB_SCRIPT.format(blob_map=blob_json)
    if '</body>' in html:
        html = html.replace('</body>', blob_script + '</body>', 1)
    else:
        html += blob_script

    # Write to a temp file first so a locked output (open in browser) doesn't
    # lose data; then atomically replace the target.
    _tmp = output_path.with_suffix('.tmp')
    _tmp.write_text(html, encoding='utf-8')
    try:
        os.replace(str(_tmp), str(output_path))
        size_mb = output_path.stat().st_size / 1_048_576
        print(f'Embedded {len(blob_map)} HTML report(s) as blob URLs')
        print(f'Wrote    {output_path}  ({size_mb:.1f} MB)')
    except PermissionError:
        size_mb = _tmp.stat().st_size / 1_048_576
        print(f'Embedded {len(blob_map)} HTML report(s) as blob URLs')
        print(f'WARNING: {output_path} is locked (close it in your browser first).')
        print(f'Wrote    {_tmp}  ({size_mb:.1f} MB)  — rename it manually.')
        output_path = _tmp
    return output_path


def main():
    ap = argparse.ArgumentParser(
        description='Make a portable self-contained Dashboard.html')
    ap.add_argument('dashboard', help='Path to Dashboard.html')
    ap.add_argument('--out', help='Output file (default: Dashboard_portable.html next to input)')
    args = ap.parse_args()

    dashboard_path = Path(args.dashboard)
    if not dashboard_path.exists():
        print(f'Error: {dashboard_path} not found', file=sys.stderr)
        sys.exit(1)

    make_portable(dashboard_path, Path(args.out) if args.out else None)


if __name__ == '__main__':
    main()
