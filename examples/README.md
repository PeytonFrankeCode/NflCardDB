# Using the data on your website

Three ways in, depending on what your site is.

| Your site | Use | Key safety |
|---|---|---|
| Static (GitHub Pages, Squarespace, plain HTML) | `browser.html` | Key is **public** — use a low quota |
| Node / Next.js / Express | `server-node.js` | Key stays on the server |
| PHP / WordPress / shared hosting | `server.php` | Key stays on the server |

## The one rule

**A key in browser JavaScript is readable by anyone who views the page source.**
The browser has to know it to send it, so the visitor knows it too. No amount of
obfuscation changes that.

That is fine — as long as you *decide* it is fine. Mint a key labelled `public`
with a small daily quota and treat it as identification, not protection. If the
key must stay secret, the call has to come from a server, which is what the
other two examples do.

Either way, mint separate keys so a leaked one can be revoked on its own:

```bash
nflcarddb api-key --label website-public
nflcarddb api-key --label website-server
```

## The other option: no API at all

If your website is the GitHub Pages dashboard in `site/`, it already reads
`site/data/*.json` directly — no key, no Cloudflare, nothing to run. Those files
are public by definition, and they update whenever you push.

The API earns its place when you need to *query* — one player, one grade, a date
range — rather than load the whole dataset.

## What the server examples deliberately do not do

They are not pass-through proxies. Forwarding arbitrary query strings would let
anyone drive your key at full speed, which defeats the point of hiding it. Each
route exposes a fixed shape with parameters you control, caches for five minutes
(sales for a past day never change), and returns a generic error rather than the
upstream body — which would otherwise reveal your quota state.
