async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error("HTTP " + r.status);
  return await r.json();
}

function movieCard(m) {
  const img = m.poster_path ? `https://image.tmdb.org/t/p/w500${m.poster_path}` : "";
  const year = (m.release_date || "").slice(0, 4);
  const score = m.vote_average ? `<span class="chip">${m.vote_average.toFixed(1)}</span>` : "";
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

document.addEventListener("DOMContentLoaded", () => {
  // ----- FILTRE ELEMANLARI -----
  const btn  = document.getElementById("fetchBtn");
  const genre = document.getElementById("genre");
  const year  = document.getElementById("year");
  const sort  = document.getElementById("sort");
  const vote  = document.getElementById("vote");

  // ----- ANA ALAN (GENİŞ) HEDEFLERİ -----
  const full      = document.getElementById("discoverFull");      // kapsayıcı
  const gridFull  = document.getElementById("discoverFullGrid");  // kartlar
  const pagerFull = document.getElementById("discoverFullPager"); // sayfalama
  const infoFull  = document.getElementById("discoverFullInfo");  // sayfa bilgisi
  const closeBtn  = document.getElementById("discoverClose");     // kapat düğmesi

  // ----- "Yeni Filmler" BLOĞU -----
  const newMovies = document.getElementById("newMovies");

  // ----- (OPSİYONEL) ESKİ YAN PANEL HEDEFLERİ: TEMİZLEMEK İÇİN -----
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

    // ---- ANA ALANA BAS / YENİ FİLMLERİ GİZLE ----
    if (full && gridFull) {
      full.classList.remove("hidden");
      gridFull.innerHTML = (data.results || []).map(movieCard).join("");

      if (infoFull)  infoFull.textContent = `Sayfa ${page} / ${Math.max(1, totalPages)}`;
      if (pagerFull) pagerFull.hidden = totalPages <= 1;

      if (newMovies) newMovies.classList.add("hidden");

      // sayfayı sonuçlara kaydır
      window.scrollTo({ top: full.offsetTop - 80, behavior: "smooth" });
    }

    // ---- YAN PANELİ TEMİZLE (kullanılmıyor) ----
    if (gridSide)  gridSide.innerHTML = "";
    if (pagerSide) pagerSide.hidden = true;
    if (infoSide)  infoSide.textContent = "";
  }

  // Filmleri Getir
  if (btn) btn.addEventListener("click", () => { page = 1; load().catch(console.error); });

  // Ana alan sayfalama
  if (pagerFull) pagerFull.addEventListener("click", (e) => {
    const dir = e.target.getAttribute("data-dir");
    if (!dir) return;
    const next = page + parseInt(dir, 10);
    if (next >= 1 && next <= totalPages) { page = next; load().catch(console.error); }
  });

  // Sonuçları kapat → Yeni Filmler geri gelsin
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
