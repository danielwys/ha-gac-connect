"""Serves the slide-puzzle page during the config flow and resumes it.

The config flow enters an external step pointing the browser at this view. The
page renders the puzzle images, the user drags the piece and presses Verify, and
the offset is POSTed back. On a correct slide the SMS is sent and the config flow
is resumed; a wrong slide swaps in a fresh puzzle without leaving the page.

Only the user solves the puzzle — nothing here measures the gap.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp
from aiohttp import web
from gac_connect.errors import CaptchaError, GacError, LoginError, RateLimitedError

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownFlow

from .const import DOMAIN

CAPTCHA_URL = "/api/gac_connect/captcha"
CAPTCHA_SECONDS = 600


def flow_state(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    return hass.data.setdefault(DOMAIN, {}).setdefault("flows", {})


def discard_flow_state(hass: HomeAssistant, flow_id: str) -> None:
    state = flow_state(hass).pop(flow_id, None)
    task = state.get("verification_task") if state else None
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()


def active_flow_state(hass: HomeAssistant, flow_id: str | None) -> dict | None:
    state = flow_state(hass).get(flow_id)
    if state is None:
        return None
    try:
        active = hass.config_entries.flow.async_get(flow_id)
    except UnknownFlow:
        active = None
    if not active or time.monotonic() >= state["expires"]:
        discard_flow_state(hass, flow_id)
        return None
    return state


_PAGE = """<!doctype html><html lang="en"><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>GAC verification</title>
<style>body{font-family:system-ui,-apple-system,sans-serif;background:#111;color:#eee;display:flex;
flex-direction:column;align-items:center;gap:16px;padding:28px 4px;margin:0;box-sizing:border-box}.st{position:relative;width:310px;height:155px;
background:#000;border-radius:8px;overflow:hidden}.st img{position:absolute;top:0;left:0}input{width:310px}
button{font-size:16px;font-weight:700;padding:10px 24px;border:0;border-radius:9px;background:#2e7d32;color:#fff}
button:disabled{background:#444}.m{min-height:1.3em}.ok{color:#4ade80;font-weight:700}.bad{color:#f87171}
.x{font-size:30px;font-weight:800}h1{font-size:1.2rem}#help,.m{max-width:42ch;line-height:1.5}input:focus-visible,button:focus-visible{outline:3px solid #a5d6a7;outline-offset:4px}</style>
<h1>Slide the piece into the gap</h1>
<div class=st><img id=b width=310 height=155 alt="Verification puzzle with a missing piece"><img id=p alt="Movable puzzle piece"></div>
<label for=s>Puzzle piece position</label>
<input id=s type=range min=0 max=263 step=1 value=0 aria-describedby=help><div class=x id=xv aria-hidden=true>0</div>
<div id=help>Drag the piece into the gap, then Verify. Focus the slider and use the arrow keys to move one pixel.</div>
<button id=g>Verify</button><div class=m id=m role=status aria-live=polite aria-atomic=true>Loading puzzle…</div>
<script>
const F=new URLSearchParams(location.search).get('flow_id');
const s=document.getElementById('s'),p=document.getElementById('p'),xv=document.getElementById('xv'),
b=document.getElementById('b'),g=document.getElementById('g'),m=document.getElementById('m');
let at=0,nid=0,done=false;const u=()=>{p.style.left=s.value+'px';xv.textContent=s.value};s.oninput=u;
const verified=()=>{done=true;g.disabled=true;m.textContent='Verified ✓ — return to Home Assistant.'};
async function poll(){if(done)return;let t;try{const r=await fetch('?flow_id='+F+'&state=1',{headers:{accept:'application/json'}});
if(done)return;
if(r.status==404){done=true;g.disabled=true;m.textContent='This sign-in has expired. Start again from Home Assistant.';return}
if(!r.ok)throw new Error(r.status);t=await r.json()}
catch(e){setTimeout(poll,2000);return}
if(done)return;
if(t.attempt!=at){at=t.attempt;b.src='data:image/png;base64,'+t.bg;p.src='data:image/png;base64,'+t.piece;s.value=0;u();g.disabled=false;m.textContent=at>1?'Not quite — new puzzle (try '+at+').':'Puzzle ready.'}
if(t.status=='ok'){verified();return}
if(t.status=='failed'){done=true;g.disabled=true;m.textContent=t.msg;return}
if(t.nid!=nid){nid=t.nid;m.innerHTML='';const sp=document.createElement('span');sp.className='bad';sp.textContent=t.notice;m.appendChild(sp);
setTimeout(()=>{if(!done)g.disabled=false},Math.max(0,t.wait)*1000)}
setTimeout(poll,500)}
g.onclick=async()=>{g.disabled=true;m.textContent='Checking…';
const fail=t=>{m.textContent=t;setTimeout(()=>{if(!done)g.disabled=false},3000)};
try{const r=await fetch('?flow_id='+F,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({x:+s.value})});
if(r.status==404){done=true;m.textContent='This sign-in has expired. Start again from Home Assistant.'}
else if(r.ok){const result=await r.json();if(result.ok)verified()}
else if(r.status!=409)fail('Something went wrong. Press Verify to try again.')}
catch(e){fail('Could not reach Home Assistant. Try again in a moment.')}};
poll();</script></html>"""


class GacCaptchaView(HomeAssistantView):
    url = CAPTCHA_URL
    name = "api:gac_connect:captcha"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        flow_id = request.query.get("flow_id")
        state = active_flow_state(hass, flow_id)
        if not state:
            return web.Response(status=404, text="expired")
        if request.query.get("state"):
            return web.json_response(_public(state))
        return web.Response(text=_PAGE, content_type="text/html")

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        flow_id = request.query.get("flow_id")
        state = active_flow_state(hass, flow_id)
        if not state:
            return web.json_response({"ok": False}, status=404)
        try:
            data = await request.json()
            x = data.get("x") if isinstance(data, dict) else None
            if isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= 263:
                raise ValueError("invalid puzzle offset")
        except (ValueError, TypeError):
            return web.json_response({"ok": False}, status=400)

        # Reading the body yielded control: the flow may have been canceled.
        if active_flow_state(hass, flow_id) is not state:
            return web.json_response({"ok": False}, status=404)

        if state.get("status") == "ok":
            return web.json_response({"ok": True})          # already verified; nothing more to send
        if state.get("busy"):
            return web.json_response({"ok": False, "busy": True}, status=409)
        client = state["client"]
        state["busy"] = True
        state["verification_task"] = asyncio.current_task()
        try:
            if state.get("need_new"):
                # the last puzzle was rejected but no new one could be fetched: fetch
                # it now; the user solves it before anything else is sent
                await _new_puzzle(client, state)
                return web.json_response({"ok": False, "retry": True})
            try:
                await client.request_sms(state["mobile"], x)
            except CaptchaError:
                # wrong slide (or a bad ticket) — hand back a fresh puzzle
                state["need_new"] = True
                await _new_puzzle(client, state)
                return web.json_response({"ok": False, "retry": True})
            except LoginError as err:
                # The puzzle was accepted but sign-in itself was refused — e.g. the
                # number is not registered on this region's backend (a UK/AION Auto
                # account lives on a different platform this library does not support).
                # A fresh puzzle cannot help, so show the reason and stop rather than
                # looping as if the slide were wrong.
                state["status"], state["msg"] = "failed", str(err)
                return web.json_response({"ok": False, "failed": True})
        except RateLimitedError as err:
            wait = int(err.retry_after or 60)
            _notice(state, f"Too many requests to GAC just now. Wait {wait} s, then press Verify again.", wait)
            return web.json_response({"ok": False, "wait": wait})
        except (GacError, aiohttp.ClientError, asyncio.TimeoutError):
            _notice(state, "Could not reach GAC. Press Verify to try again.", 3)
            return web.json_response({"ok": False})
        finally:
            state["busy"] = False
            state.pop("verification_task", None)
        state["status"] = "ok"
        hass.async_create_task(
            hass.config_entries.flow.async_configure(flow_id=flow_id, user_input={})
        )
        return web.json_response({"ok": True})


async def _new_puzzle(client: Any, state: dict[str, Any]) -> None:
    state["captcha"] = await client.start_captcha()
    state["need_new"] = False
    state["attempt"] += 1


def _notice(state: dict[str, Any], text: str, wait: int) -> None:
    """A message for the puzzle page; Verify re-enables once ``wait`` seconds pass."""
    state["notice"], state["notice_until"] = text, time.monotonic() + wait
    state["nid"] = state.get("nid", 0) + 1


def _public(state: dict[str, Any]) -> dict[str, Any]:
    c = state["captcha"]
    return {
        "nid": state.get("nid", 0),
        "notice": state.get("notice", ""),
        "wait": max(0, round(state.get("notice_until", 0) - time.monotonic())),
        "attempt": state["attempt"],
        "status": state.get("status", "pending"),
        "msg": state.get("msg", ""),
        "bg": c.background,
        "piece": c.piece,
    }
