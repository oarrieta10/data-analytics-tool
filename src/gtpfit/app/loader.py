"""loader.py — the HAI robot loading animation, shown in the middle of the screen while a page is working.

Replaces Streamlit's running-man icon (next to Stop / Deploy).  install() is called at the top of the
workbench on every run; the script it adds installs itself once per browser tab, then watches the app's
data-test-script-state and shows the <hai-loader> overlay when a run lasts longer than SHOW_AFTER_MS, so quick
reruns (ticking a box) don't flash it.  The overlay sits under Streamlit's header, so Stop and the top-bar
progress bar stay visible.  The component and three.js are served from app/static/ (no internet needed).
"""
from __future__ import annotations

import streamlit as st

SHOW_AFTER_MS = 600     # UI timing: runs shorter than this show no overlay
BACKDROP_OPACITY = 0.85  # white veil behind the robot; the page shows faintly through it

LOADER_HTML = """
<script>
(() => {
  if (window.__haiLoaderInstalled) return;
  window.__haiLoaderInstalled = true;
  const SHOW_AFTER_MS = %(delay)d;
  import('/app/static/hai-loader/hai-loader.js').catch((e) => console.warn('hai-loader not loaded', e));
  const style = document.createElement('style');
  style.textContent = `
    [data-testid="stStatusWidgetRunningIcon"], [data-testid="stStatusWidgetRunningManIcon"],
    [data-testid="stStatusWidgetNewYearsIcon"] { display: none !important; }
    #hai-loading { position: fixed; inset: 0; z-index: 999980; display: none;
      background: rgba(255, 255, 255, %(opacity)s); }
    #hai-loading.on { display: block; }
    #hai-loading hai-loader { position: absolute; inset: 0; }
  `;
  document.head.appendChild(style);
  const overlay = document.createElement('div');
  overlay.id = 'hai-loading';
  document.body.appendChild(overlay);
  const robot = document.createElement('hai-loader');   // added only while shown: no rendering when idle
  let timer = null;
  const show = () => { timer = null; if (!robot.isConnected) overlay.appendChild(robot); overlay.classList.add('on'); };
  const hide = () => { clearTimeout(timer); timer = null; overlay.classList.remove('on'); robot.remove(); };
  const update = () => {
    const app = document.querySelector('[data-test-script-state]');
    const state = app && app.getAttribute('data-test-script-state');
    if (state === 'running' || state === 'rerunRequested') {
      if (!timer && !overlay.classList.contains('on')) timer = setTimeout(show, SHOW_AFTER_MS);
    } else {
      hide();
    }
  };
  new MutationObserver(update).observe(document.body,
    { subtree: true, attributes: true, attributeFilter: ['data-test-script-state'] });
  update();
})();
</script>
""" % {"delay": SHOW_AFTER_MS, "opacity": BACKDROP_OPACITY}


def install() -> None:
    st.html(LOADER_HTML, unsafe_allow_javascript=True)
