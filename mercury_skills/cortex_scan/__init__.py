"""mercury_skills.cortex_scan — Mercury tool: end-to-end Cortex scan from chat.

Flow when Mercury sees a scan request (Discord attachment, web upload, CLI arg):

  1. POST file → https://big-apple.scylla-betta.ts.net/api/scan
  2. Poll /api/scan/<id> until status == "complete" (or "failed", or timeout)
  3. Use the browser skill to screenshot the 3D brain at the scan URL
  4. Return a structured response: {scan_id, top_rois, peak_t, narrations,
     brain_screenshot_b64, gallery_url}

Mercury's surface adapters (Discord / web / CLI) decide how to render the
result — Discord posts narrations as text + screenshot as image attachment;
the web dashboard inlines the screenshot; the CLI prints text.
"""
from .scan import submit_and_wait, ScanResult, ScanError

__all__ = ["submit_and_wait", "ScanResult", "ScanError"]
