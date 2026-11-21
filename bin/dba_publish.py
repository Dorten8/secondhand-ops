#!/usr/bin/env python3
"""
dba_publish.py — publish one item from the sales database to DBA.

Deliberate design choices:

* It does NOT use Playwright. The browser that already holds the DBA session is on
  this box (CDP on 127.0.0.1:9222), so the publisher attaches to it. No cookie jar
  is written to disk, nothing to expire, and the session is whatever the human
  already logged into.
* It clicks with real mouse events. DBA's UI is a web-component tree inside shadow
  roots; element `.click()` silently does nothing on the category cards, and the
  next step never happens.
* It is a wizard driver, not one script per item type: categories are passed in,
  because "Sport og friluftsliv > Cykling" and "Elektronik > Telefoner > Mobil-
  telefoner" are different paths through the same form.
* It stops before the final button unless --confirm is passed, and it always
  screenshots the step it stops at.

Usage:
    python3 bin/dba_publish.py --json item.json --dry-run
    python3 bin/dba_publish.py --json item.json --confirm

item.json:
    {
      "title": "...", "description": "...", "price": 3600, "postal": "2300",
      "photos": ["/tmp/a.jpg", "..."],
      "category": {"main": "93", "sub": "3217", "kind": "39"},
      "condition": "2",
      "attributes": {"brand": "Google", "storage": "256 GB"}
    }
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Browser  # noqa: E402

SHADOW_WALK = """
  const all=[];
  const walk=(r)=>{r.querySelectorAll('*').forEach(e=>{all.push(e); if(e.shadowRoot) walk(e.shadowRoot);});};
  walk(document);
"""


class Publisher:
    def __init__(self, page, verbose: bool = True):
        self.page = page
        self.verbose = verbose

    def log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)

    # ---- primitives ---------------------------------------------------
    async def clicks_by_text(self, text: str, exact: bool = True) -> dict:
        """Real mouse click on an element containing `text`. Returns its rect.

        Two details that matter on DBA's form:
          * the target must be scrolled into view FIRST — a rect below the fold
            produces a click that lands on whatever is actually at those
            coordinates, which silently does nothing;
          * the click must be a real Input.dispatchMouseEvent, because these are
            web components behind shadow roots and `.click()` is ignored.
        """
        find = f"""(() => {{{SHADOW_WALK}
          const want={json.dumps(text)};
          const hits=all.filter(e=>{{const t=(e.innerText||'').trim(); return {str(exact).lower()} ? t===want : t.includes(want);}});
          if(!hits.length) return null;
          const el=hits[hits.length-1];
          window.__dbaTarget = el;
          el.scrollIntoView({{block:'center', inline:'center'}});
          return {{tag:el.tagName}};
        }})()"""
        found = await self.page.eval(find)
        if not found:
            return {"clicked": False, "reason": f"no element with text {text!r}"}
        await asyncio.sleep(0.5)
        rect = await self.page.eval(
            """(() => { const el=window.__dbaTarget; if(!el) return null;
                 const r=el.getBoundingClientRect();
                 return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
                         w:Math.round(r.width), h:Math.round(r.height),
                         inView: r.top>=0 && r.bottom<=innerHeight}; })()"""
        )
        if not rect:
            return {"clicked": False, "reason": "target vanished after scroll"}
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.page._send(
                "Input.dispatchMouseEvent",
                {"type": t, "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1},
            )
            await asyncio.sleep(0.12)
        return {"clicked": True, "rect": rect, "tag": found.get("tag")}

    async def select_by_index(self, index: int, value: str) -> dict:
        js = f"""(() => {{{SHADOW_WALK}
          const s=all.filter(e=>e.tagName==='SELECT')[{index}];
          if(!s) return {{ok:false, reason:'no select at index'}};
          s.value={json.dumps(value)};
          s.dispatchEvent(new Event('input',{{bubbles:true}}));
          s.dispatchEvent(new Event('change',{{bubbles:true}}));
          return {{ok:true, value:s.value, label:(s.options[s.selectedIndex]||{{}}).text||''}};
        }})()"""
        return await self.page.eval(js)

    async def select_by_option_text(self, text: str) -> dict:
        # built without an f-string: JS braces and f-string fields do not mix safely
        js = (
            "(() => {" + SHADOW_WALK +
            "  const want=" + json.dumps(text.lower()) + ";"
            "  const sels=all.filter(e=>e.tagName==='SELECT');"
            "  for(const s of sels){"
            "    const o=[...s.options].find(o=>o.text.trim().toLowerCase()===want);"
            "    if(o){ s.value=o.value;"
            "      s.dispatchEvent(new Event('input',{bubbles:true}));"
            "      s.dispatchEvent(new Event('change',{bubbles:true}));"
            "      return {ok:true, value:s.value, label:o.text.trim()}; }"
            "  }"
            "  return {ok:false, reason:'option not found'};"
            "})()"
        )
        return await self.page.eval(js)

    async def fill(self, args: dict) -> dict:
        """Fill title, description, price, postal. Targets by name/label, not index."""
        return await self.page.eval(
            f"""(() => {{ {SHADOW_WALK}
              const args={json.dumps(args)};
              const set=(el,v)=>{{ const st=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value').set;
                st.call(el,v); el.dispatchEvent(new Event('input',{{bubbles:true}})); el.dispatchEvent(new Event('change',{{bubbles:true}})); }};
              const ins=all.filter(e=>e.tagName==='INPUT');
              const title=ins.find(i=>i.name==='title');
              if(title) set(title, args.title);
              const nums=ins.filter(i=>i.type==='number');
              if(nums[0]) set(nums[0], String(args.price));
              if(nums[1] && args.postal) set(nums[1], String(args.postal));
              const tas=all.filter(e=>e.tagName==='TEXTAREA');
              const desc=tas.find(t=>(t.placeholder||'').trim()!=='Billedtekst') || tas[tas.length-1];
              if(desc) set(desc, args.description);
              tas.filter(t=>(t.placeholder||'').trim()==='Billedtekst').forEach(t=>{{ if(t.value) set(t,''); }});
              return {{title:(title||{{}}).value||null, descLen:((desc||{{}}).value||'').length,
                       price:(nums[0]||{{}}).value||null, postal:(nums[1]||{{}}).value||null}};
            }})()"""
        )

    async def upload(self, files: list[str]) -> dict:
        js = f"""(() => {{{SHADOW_WALK}
          return all.find(e=>e.tagName==='INPUT' && e.type==='file') || null;
        }})()"""
        obj = await self.page._send("Runtime.evaluate", {"expression": js, "returnByValue": False})
        oid = obj["result"].get("objectId")
        if not oid:
            return {"ok": False, "reason": "no file input"}
        node = await self.page._send("DOM.describeNode", {"objectId": oid})
        await self.page._send(
            "DOM.setFileInputFiles", {"files": files, "backendNodeId": node["node"]["backendNodeId"]}
        )
        return {"ok": True, "count": len(files)}

    async def state(self) -> dict:
        js = f"""(() => {{{SHADOW_WALK}
          const sels=all.filter(e=>e.tagName==='SELECT').map(s=>(s.options[s.selectedIndex]||{{}}).text||'');
          const btns=[...new Set(all.filter(e=>['BUTTON','W-BUTTON'].includes(e.tagName)).map(e=>(e.innerText||'').trim()).filter(Boolean))];
          return {{url:location.href, selects:sels, buttons:btns,
                   photos:all.filter(e=>e.tagName==='IMG').length}};
        }})()"""
        return await self.page.eval(js)


async def run(args) -> int:
    item = json.loads(Path(args.json).read_text())
    browser = Browser(args.endpoint)
    page = browser.new_page("https://www.dba.dk/create-item/start")
    await page._open()
    pub = Publisher(page, verbose=True)
    try:
        await page.goto("https://www.dba.dk/create-item/start", wait=6)

        pub.log("step 0: choose Markedspladsen")
        print(" ", await pub.clicks_by_text("Markedspladsen", exact=False))
        await asyncio.sleep(6)
        url = await page.eval("location.href")
        ad_id = url.rstrip("/").split("/")[-1] if "/recommerce/create/" in url else None
        pub.log(f"  url: {url}  ad_id: {ad_id}")
        if not ad_id:
            print("STOP: did not reach the create form")
            print("shot:", await page.shot("/tmp/dba_stop_0.png"))
            return 1

        pub.log("step 1: categories")
        cat = item["category"]
        print(" ", await pub.select_by_index(0, cat["main"]))
        await asyncio.sleep(2)
        print(" ", await pub.select_by_index(1, cat["sub"]))
        await asyncio.sleep(2)
        print(" ", await pub.select_by_index(2, cat["kind"]))
        await asyncio.sleep(2)
        print(" ", await pub.select_by_index(3, item["condition"]))
        await asyncio.sleep(2)
        for label, text in (item.get("attributes") or {}).items():
            print(f"  {label}:", await pub.select_by_option_text(text))
            await asyncio.sleep(1.5)

        pub.log("step 1: photos")
        print(" ", await pub.upload(item["photos"]))
        await asyncio.sleep(6)

        pub.log("step 1: text fields")
        print(" ", await pub.fill(item))
        await asyncio.sleep(2)

        st = await pub.state()
        pub.log(f"  state: {json.dumps(st, ensure_ascii=False)[:400]}")
        print("shot:", await page.shot("/tmp/dba_step1.png"))

        if args.dry_run:
            print("DRY RUN — stopping before Fortsæt. Form left open for inspection.")
            return 0

        pub.log("step 1 -> 2: Fortsæt")
        print(" ", await pub.clicks_by_text("Fortsæt"))
        await asyncio.sleep(5)
        print("shot:", await page.shot("/tmp/dba_step2.png"))
        st = await pub.state()
        pub.log(f"  after Fortsæt: {json.dumps(st, ensure_ascii=False)[:300]}")

        if args.stop_after_first:
            print("STOP after first Fortsæt (per flag).")
            return 0

        pub.log("step 2: no shipping")
        print(" ", await pub.clicks_by_text("Jeg kan ikke sende varen", exact=False))
        await asyncio.sleep(2)
        print(" ", await pub.clicks_by_text("Fortsæt"))
        await asyncio.sleep(5)
        print("shot:", await page.shot("/tmp/dba_step3.png"))

        pub.log("step 3: Basis package")
        print(" ", await pub.clicks_by_text("Basis", exact=False))
        await asyncio.sleep(2)

        if not args.confirm:
            st = await pub.state()
            pub.log(f"READY (not confirmed): {json.dumps(st, ensure_ascii=False)[:300]}")
            print("shot:", await page.shot("/tmp/dba_ready.png"))
            print("Not clicked 'Opret annoncen' — rerun with --confirm to publish.")
            return 0

        pub.log("PUBLISH: Opret annoncen")
        print(" ", await pub.clicks_by_text("Opret annoncen", exact=False))
        await asyncio.sleep(8)
        final = await page.eval("location.href")
        print("final url:", final)
        print("shot:", await page.shot("/tmp/dba_published.png"))
        print(json.dumps({"ad_id": ad_id, "url": final}, ensure_ascii=False))
        return 0
    finally:
        await page.close()


def main():
    ap = argparse.ArgumentParser(description="Publish one item to DBA through the live browser.")
    ap.add_argument("--json", required=True, help="item payload json")
    ap.add_argument("--endpoint", default="http://127.0.0.1:9222")
    ap.add_argument("--dry-run", action="store_true", help="fill everything, stop before Fortsæt")
    ap.add_argument("--stop-after-first", action="store_true", help="stop after the first Fortsæt")
    ap.add_argument("--confirm", action="store_true", help="actually click 'Opret annoncen'")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
