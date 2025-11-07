async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error("HTTP " + r.status);
  return await r.json();
}

function movieCard(m) {
  const img = m.poster_path ? `https://image.tmdb.org/t/p/w500${m.poster_path}` : "";
  const year = (m.release_date || "").slice(0, 4);
  const score = (m.vote_average != null) ? `<span class="chip">${m.vote_average.toFixed(1)}</span>` : "";
  return `
    <a href="/movie/${m.id}" class="group">
      <img class="poster w-full aspect-[2/3] object-cover" src="${img}">
      <div class="mt-2 flex items-center justify-between">
        <div class="font-semibold group-hover:text-sky-400 truncate">${m.title}</div>
        ${score}
      </div>
      <div class="text-slate-400 text-sm">${year}</div>
    </a>
  `;
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

document.addEventListener("DOMContentLoaded", () => {
  // ----- FILTRE ELEMANLARI -----
  const btn  = document.getElementById("fetchBtn");
  const genre = document.getElementById("genre");
  const year  = document.getElementById("year");
  const sort  = document.getElementById("sort");
  const vote  = document.getElementById("vote");

  // ----- ANA ALAN (GENİŞ) HEDEFLERİ -----
  const full      = document.getElementById("discoverFull");
  const gridFull  = document.getElementById("discoverFullGrid");
  const pagerFull = document.getElementById("discoverFullPager");
  const infoFull  = document.getElementById("discoverFullInfo");
  const closeBtn  = document.getElementById("discoverClose");

  // ----- "Yeni Filmler" BLOĞU -----
  const newMovies = document.getElementById("newMovies");

  // ----- (OPSİYONEL) YAN PANEL -----
  const gridSide  = document.getElementById("discover");
  const pagerSide = document.getElementById("discover-pager");
  const infoSide  = document.getElementById("discover-info");

  let page = 1, totalPages = 1;

  async function load() {
    const qs = new URLSearchParams({ page, sort_by: sort?.value || "popularity.desc" });
    if (genre?.value) qs.set("genre_id", genre.value);
    if (year?.value)  qs.set("year", year.value);
    if (vote?.value)  qs.set("vote_gte", vote.value);

    const data = await fetchJson(`/api/discover?${qs.toString()}`);
    totalPages = data.total_pages || 1;

    if (full && gridFull) {
      full.classList.remove("hidden");
      gridFull.innerHTML = (data.results || []).map(movieCard).join("");

      if (infoFull)  infoFull.textContent = `Sayfa ${page} / ${Math.max(1, totalPages)}`;
      if (pagerFull) pagerFull.hidden = totalPages <= 1;

      if (newMovies) newMovies.classList.add("hidden");
      window.scrollTo({ top: full.offsetTop - 80, behavior: "smooth" });
    }

    if (gridSide)  gridSide.innerHTML = "";
    if (pagerSide) pagerSide.hidden = true;
    if (infoSide)  infoSide.textContent = "";
  }

  if (btn) btn.addEventListener("click", () => { page = 1; load().catch(console.error); });

  if (pagerFull) pagerFull.addEventListener("click", (e) => {
    const dir = e.target.getAttribute("data-dir");
    if (!dir) return;
    const next = page + parseInt(dir, 10);
    if (next >= 1 && next <= totalPages) { page = next; load().catch(console.error); }
  });

  if (closeBtn) closeBtn.addEventListener("click", () => {
    if (full) {
      full.classList.add("hidden");
      if (gridFull) gridFull.innerHTML = "";
      if (pagerFull) pagerFull.hidden = true;
      if (infoFull)  infoFull.textContent = "";
    }
    if (newMovies) newMovies.classList.remove("hidden");
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  // ---------- Canlı Arama Önerisi ----------
  const form  = document.getElementById("siteSearchForm");
  const input = document.getElementById("siteSearchInput");
  const box   = document.getElementById("searchSuggest");

  let items = [];
  let active = -1;

  function render(list) {
    if (!list.length) { box.classList.add("hidden"); box.innerHTML = ""; return; }
    box.innerHTML = list.map((m, idx) => {
      const img = m.poster_path ? `https://image.tmdb.org/t/p/w185${m.poster_path}` : "";
      const year = (m.release_date || "").slice(0,4);
      const score = (m.vote_average != null) ? m.vote_average.toFixed(1) : "";
      return `
        <a href="/movie/${m.id}" class="suggest-item ${idx===active?'active':''}" data-idx="${idx}">
          <img src="${img}" class="w-12 h-16 object-cover rounded-md" onerror="this.style.display='none'">
          <div class="min-w-0">
            <div class="text-slate-100 font-medium truncate">${m.title || ""}</div>
            <div class="text-slate-400 text-sm flex items-center gap-2">
              ${year ? `<span>${year}</span>` : ""}
              ${score ? `<span>IMDB: <span class="text-amber-300">${score}</span></span>` : ""}
            </div>
          </div>
        </a>`;
    }).join("");
    box.classList.remove("hidden");
    box.querySelectorAll("a.suggest-item").forEach(a => {
      a.addEventListener("mousemove", () => {
        active = parseInt(a.dataset.idx, 10);
        highlight();
      });
    });
  }

  function highlight() {
    box.querySelectorAll("a.suggest-item").forEach((a,i) => {
      if (i === active) a.classList.add("active"); else a.classList.remove("active");
    });
  }

  const doSearch = debounce(async (q) => {
    if (!q || q.length < 2) { box.classList.add("hidden"); box.innerHTML = ""; return; }
    try {
      const r = await fetch(`/api/search_suggest?q=${encodeURIComponent(q)}`);
      const data = await r.json();
      items = data.results || [];
      active = -1;
      render(items);
    } catch (e) { /* sessiz fail */ }
  }, 250);

  if (input && box) {
    input.addEventListener("input", (e) => doSearch(e.target.value));

    input.addEventListener("keydown", (e) => {
      if (box.classList.contains("hidden")) return;
      const max = items.length - 1;
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(max, active + 1); highlight(); scrollActiveIntoView(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(-1, active - 1); highlight(); scrollActiveIntoView(); }
      else if (e.key === "Enter") {
        if (active >= 0 && items[active]) {
          e.preventDefault();
          window.location.href = `/movie/${items[active].id}`;
        }
      } else if (e.key === "Escape") {
        box.classList.add("hidden");
      }
    });

    function scrollActiveIntoView() {
      const el = box.querySelector("a.suggest-item.active");
      if (el) {
        const r = el.getBoundingClientRect();
        const rb = box.getBoundingClientRect();
        if (r.bottom > rb.bottom) el.scrollIntoView({ block: "end" });
        if (r.top < rb.top) el.scrollIntoView({ block: "start" });
      }
    }

    input.addEventListener("focus", () => { if (items.length) box.classList.remove("hidden"); });
    input.addEventListener("blur", () => setTimeout(() => box.classList.add("hidden"), 120));

    document.addEventListener("click", (e) => {
      if (!form.contains(e.target)) box.classList.add("hidden");
    });
  }

  // ----- Trailer modal -----
  const tbtn  = document.getElementById("watchTrailer");
  const modal = document.getElementById("trailerModal");
  const frame = document.getElementById("trailerFrame");
  if (tbtn && modal && frame) {
    const key = tbtn.getAttribute("data-key");
    tbtn.addEventListener("click", () => {
      frame.src = `https://www.youtube.com/embed/${key}?autoplay=1`;
      modal.classList.add("open");
    });
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        frame.src = "";
        modal.classList.remove("open");
      }
    });
  }
});
