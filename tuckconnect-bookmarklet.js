// TuckConnect → Cold Email Agent bookmarklet
//
// DOM structure (confirmed from live page HTML):
//
//   <div class="col-md-6">               ← left col: name + details
//     <br>
//     <a href="...profile?p_id=..."><b>Harry L. Alverson, IV T'10</b></a>
//      (Harry)
//     <br>
//     IBM Corporation<br>
//     <b>Title:</b> VP, Product Marketing, IBM Automation<br>
//     <b>Function:</b> Marketing - Brand/Product Management<br>
//     <b>Industry:</b> Technology - Other<br>
//     <br>
//   </div>
//   <div class="col-md-6">               ← right col: email
//     <a href="mailto:harry@example.com">harry@example.com</a>
//   </div>
//
// SETUP: Add as a browser bookmark. In Chrome:
//   1. Bookmark any page
//   2. Edit the bookmark — name it "TuckConnect Export"
//   3. Replace the URL with the minified javascript: URI below
//
// MINIFIED URI (copy this entire line into the bookmark URL field):
// javascript:(function(){var ps=document.querySelectorAll('a[href*="tuckconnect_alumni_search.profile"]');if(!ps.length){alert('No contacts found. Are you on a TuckConnect results page?');return;}function cn(s){return s.replace(/\s*T['’]?\d{2}\s*/g,' ').replace(/\s*\([^)]+\)\s*$/,'').replace(/\s+/g,' ').trim();}function gf(t,k){var m=t.match(new RegExp(k+'[:\\s]+([^\\n\\r]+)','i'));return m?m[1].replace(/\s+/g,' ').trim():null;}var c=[],seen={};ps.forEach(function(nl){var col=nl.closest('.col-md-6')||nl.parentElement;var row=col?col.parentElement:null;if(!row)return;var em=(row.querySelector('a[href^="mailto:"]')||{textContent:'',href:''});var email=(em.textContent||'').trim()||em.href.replace('mailto:','');if(!email||seen[email])return;seen[email]=true;var raw=(nl.textContent||'').trim();var name=cn(raw);var ct=col.textContent||'';var ai=ct.indexOf(raw)+raw.length;var an=ct.substring(ai).replace(/^\s*\([^)]+\)\s*/,'');var co=an.split(/\s*(?:Title|Function|Industry):/i)[0].replace(/\s+/g,' ').trim();var role=gf(ct,'Title');var fn=gf(ct,'Function');var ind=gf(ct,'Industry');var notes=[fn&&'Function: '+fn,ind&&'Industry: '+ind].filter(Boolean).join(' | ');c.push({name:name||null,email:email||null,company:co||null,role:role||null,notes:notes||null,dartmouth:true,mode:'outreach',tier:2});});if(!c.length){alert('Could not parse contacts. Open DevTools (F12) for debug info.');console.log('[TuckConnect] profile links:',ps.length);console.log('[TuckConnect] first link col HTML:',(ps[0]&&ps[0].closest('.col-md-6')||ps[0]&&ps[0].parentElement||{outerHTML:''}).outerHTML.slice(0,600));return;}var j=JSON.stringify(c,null,2);navigator.clipboard.writeText(j).then(function(){alert('Copied '+c.length+' contacts to clipboard.\nPaste into the Import page in your contact manager.');}).catch(function(){var w=window.open('','_blank','width=700,height=500');w.document.write('<html><body style="margin:0"><textarea style="width:100%;height:100%;font-family:monospace;font-size:12px;border:none;padding:8px">'+j.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</textarea></body></html>');alert('Clipboard blocked. Copy the JSON from the new window that opened.');});})();
//
// ─────────────────────────────────────────────────────────────────────────────
// READABLE SOURCE (keep this for debugging / updating selectors)
// ─────────────────────────────────────────────────────────────────────────────

(function () {
  // ── 1. Find all name links (profile page links) ────────────────────────────
  // Name links have href containing "tuckconnect_alumni_search.profile"
  // The text inside is <b>Harry L. Alverson, IV T'10</b>
  var profileLinks = document.querySelectorAll(
    'a[href*="tuckconnect_alumni_search.profile"]'
  );

  if (profileLinks.length === 0) {
    alert(
      "No contacts found. Make sure you are on a TuckConnect search results " +
        "page with results visible."
    );
    return;
  }

  // ── 2. Clean name: strip graduation year (T'10) and any trailing nickname ──
  // Input:  "Harry L. Alverson, IV T'10"
  // Output: "Harry L. Alverson, IV"
  function cleanName(raw) {
    return raw
      .replace(/\s*T['']?\d{2}\s*/g, " ") // strip T'10 / T'07 etc.
      .replace(/\s*\([^)]+\)\s*$/, "")          // strip trailing (Nickname) if inside tag
      .replace(/\s+/g, " ")
      .trim();
  }

  // ── 3. Extract labeled field ("Title:", "Function:", "Industry:") ──────────
  function getField(text, label) {
    var re = new RegExp(label + "[:\\s]+([^\\n\\r]+)", "i");
    var m = text.match(re);
    return m ? m[1].replace(/\s+/g, " ").trim() : null;
  }

  // ── 4. Parse each contact ─────────────────────────────────────────────────
  var contacts = [];
  var seen = {};

  profileLinks.forEach(function (nameLink) {
    // The name link is inside a .col-md-6 div (the left details column)
    var col = nameLink.closest(".col-md-6") || nameLink.parentElement;
    // The parent of that col is the row — the right col-md-6 sibling has the email
    var row = col ? col.parentElement : null;
    if (!row) return;

    var emailLink = row.querySelector('a[href^="mailto:"]');
    var email = emailLink
      ? (emailLink.textContent || "").trim() ||
        emailLink.href.replace("mailto:", "")
      : null;

    if (!email || seen[email]) return;
    seen[email] = true;

    // Name: textContent of the <b> inside the <a> → "Harry L. Alverson, IV T'10"
    var rawName = (nameLink.textContent || "").trim();
    var name = cleanName(rawName);

    // Company: text in the left col after the name link, before first labeled field
    // The nickname "(Harry)" is a text node right after </a> — strip it
    var colText = col.textContent || "";
    var afterNameIdx = colText.indexOf(rawName) + rawName.length;
    var afterName = colText.substring(afterNameIdx);
    afterName = afterName.replace(/^\s*\([^)]+\)\s*/, ""); // strip "(Nickname)\n"
    var company = afterName
      .split(/\s*(?:Title|Function|Industry):/i)[0]
      .replace(/\s+/g, " ")
      .trim();

    var role = getField(colText, "Title");
    var fn = getField(colText, "Function");
    var industry = getField(colText, "Industry");
    var notes = [fn && "Function: " + fn, industry && "Industry: " + industry]
      .filter(Boolean)
      .join(" | ");

    contacts.push({
      name: name || null,
      email: email || null,
      company: company || null,
      role: role || null,
      notes: notes || null,
      dartmouth: true,
      mode: "outreach",
      tier: 2,
    });
  });

  // ── 5. Debug output if nothing parsed ─────────────────────────────────────
  if (contacts.length === 0) {
    alert(
      "Could not parse any contacts. Open DevTools (F12 → Console) for debug info."
    );
    console.log("[TuckConnect] profile links found:", profileLinks.length);
    var firstCol =
      profileLinks[0] &&
      (profileLinks[0].closest(".col-md-6") || profileLinks[0].parentElement);
    console.log(
      "[TuckConnect] First contact col HTML:",
      firstCol ? firstCol.outerHTML.slice(0, 600) : "not found"
    );
    return;
  }

  // ── 6. Copy JSON to clipboard ──────────────────────────────────────────────
  var json = JSON.stringify(contacts, null, 2);

  navigator.clipboard
    .writeText(json)
    .then(function () {
      alert(
        "Copied " +
          contacts.length +
          " contacts to clipboard.\n" +
          "Paste into the Import page in your contact manager."
      );
    })
    .catch(function () {
      // Fallback: open result in a new window if clipboard permission is denied
      var win = window.open("", "_blank", "width=700,height=500");
      win.document.write(
        "<html><body style='margin:0'>" +
          "<textarea style='width:100%;height:100%;font-family:monospace;" +
          "font-size:12px;border:none;padding:8px'>" +
          json.replace(/&/g, "&amp;").replace(/</g, "&lt;") +
          "</textarea></body></html>"
      );
      alert(
        "Clipboard blocked. Copy the JSON from the new window that opened."
      );
    });
})();
