/*
 * NflCardDB page grabber -- a bookmarklet.
 *
 * Runs inside your own browser, on a page you are already looking at, when you
 * click it. There is no automation and nothing to sign in to: you are signed in
 * already, because it is your browser.
 *
 * It reads the listings off the page you are on and downloads them as a small
 * JSON file. Drag that file onto import.bat.
 *
 * Reading the live DOM beats parsing saved HTML: the page has already run its
 * scripts, so prices and sold dates are final rather than placeholders.
 */
(function () {
  var out = [];
  var seen = {};

  function textOf(el) {
    return el ? (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim() : "";
  }

  function closestTile(a) {
    var n = a;
    for (var i = 0; i < 8 && n; i++) {
      n = n.parentElement;
      if (!n) break;
      if (n.tagName === "LI") return n;
      var c = n.className || "";
      if (typeof c === "string" && /s-item__wrapper|s-card__wrapper/.test(c)) return n;
    }
    return a.parentElement || a;
  }

  var links = document.querySelectorAll('a[href*="/itm/"]');
  for (var i = 0; i < links.length; i++) {
    var href = links[i].getAttribute("href") || "";
    var m = href.match(/\/itm\/(?:[^/?#]+\/)?(\d{9,15})/);
    if (!m) continue;
    var id = m[1];
    if (seen[id]) continue;

    var tile = closestTile(links[i]);
    var blob = textOf(tile);
    if (!blob) continue;

    // Title: prefer the heading, fall back to the link's own text.
    var tEl = tile.querySelector(
      '.s-item__title span[role=heading], .s-item__title, .s-card__title span, ' +
      '.s-card__title, [class*=item__title], [class*=card__title], h3'
    );
    var title = textOf(tEl) || links[i].getAttribute("aria-label") || textOf(links[i]);
    title = title.replace(/^new listing\s*/i, "").trim();
    if (!title || /^shop on ebay$/i.test(title)) continue;

    // Price: skip anything that is plainly a shipping line.
    var pEl = tile.querySelector(
      '.s-item__price, .s-card__price, [class*=item__price], [class*=card__price], ' +
      '.su-styled-text.primary.bold'
    );
    var priceText = textOf(pEl);
    if (!priceText) {
      var lines = blob.split(/\s{2,}|\n/);
      for (var j = 0; j < lines.length; j++) {
        if (/[$£€¥]\s?\d/.test(lines[j]) && !/shipping|postage|delivery/i.test(lines[j])) {
          priceText = lines[j];
          break;
        }
      }
    }

    var soldM = blob.match(/sold\s+([A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})/i);
    var bidsM = blob.match(/(\d+)\s+bids?\b/i);

    seen[id] = 1;
    out.push({
      id: id,
      title: title,
      price_text: priceText,
      sold_text: soldM ? soldM[1] : null,
      bids: bidsM ? parseInt(bidsM[1], 10) : null,
      best_offer: /best\s+offer/i.test(blob),
      shipping_text: (blob.match(/(free\s+(?:shipping|postage|delivery)|\+\s*[$£€¥]\s?[\d,.]+\s*(?:shipping|postage|delivery))/i) || [null])[0]
    });
  }

  if (!out.length) {
    alert(
      "NflCardDB: no listings found on this page.\n\n" +
      "Make sure you are on an eBay SEARCH RESULTS page with the " +
      "'Sold items' filter switched on."
    );
    return;
  }

  var payload = {
    source: "bookmarklet",
    version: 1,
    url: location.href,
    captured_at: new Date().toISOString(),
    sales: out
  };

  var blob = new Blob([JSON.stringify(payload)], { type: "application/json" });
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "nflcarddb-" + Date.now() + ".json";
  document.body.appendChild(a);
  a.click();
  setTimeout(function () {
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  }, 1000);

  alert("NflCardDB: captured " + out.length + " listings.\n\nDrag the downloaded file onto import.bat");
})();
