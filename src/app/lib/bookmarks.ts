// src/app/lib/bookmarks.ts
// 북마크(즐겨찾기) 공용 저장소 — 경로 북마크 + 지하철/버스 혼잡도 북마크를 한 곳에서 관리합니다.
// localStorage 사용 (기기별 저장). 새로운 종류를 추가해도 기존 코드에 영향 없도록
// kind로 구분하는 판별 유니온(discriminated union) 구조로 만들었습니다.
//
// ── 버스 혼잡도 담당자용 안내 ──────────────────────────────────────
// 버스 혼잡도 카드에 별 아이콘을 추가하고, 클릭 시 아래처럼 호출하면
// 마이교통 탭에 자동으로 노출됩니다 (MyTransitTab 수정 불필요):
//
//   toggleCongestionBookmark({
//     kind: "busCongestion",
//     name: item["정류장명"],
//     line: item["버스번호"],
//     direction: item["방향"],
//   });
// ────────────────────────────────────────────────────────────────

const BOOKMARK_KEY = "transitBookmarks";

export type RouteBookmark = {
  id: string;
  kind: "route";
  departure: string;
  arrival: string;
  estimatedTimeMin?: number;
  originalTimeMin?: number;
  mode?: "accessibility" | "general";
  savedAt: number;
};

export type CongestionBookmark = {
  id: string;
  kind: "subwayCongestion" | "busCongestion";
  name: string; // 지하철역명 또는 버스정류장명
  line: string; // "2호선" 또는 버스 노선번호
  direction?: string; // 상행/하행 등
  savedAt: number;
};

export type TransitBookmark = RouteBookmark | CongestionBookmark;

function loadAll(): TransitBookmark[] {
  try {
    return JSON.parse(localStorage.getItem(BOOKMARK_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveAll(list: TransitBookmark[]) {
  localStorage.setItem(BOOKMARK_KEY, JSON.stringify(list));
}

export function getBookmarks(): TransitBookmark[] {
  return loadAll().sort((a, b) => b.savedAt - a.savedAt);
}

export function getRouteBookmarks(): RouteBookmark[] {
  return getBookmarks().filter((b): b is RouteBookmark => b.kind === "route");
}

export function getCongestionBookmarks(
  kind?: "subwayCongestion" | "busCongestion"
): CongestionBookmark[] {
  return getBookmarks()
    .filter(
      (b): b is CongestionBookmark =>
        b.kind === "subwayCongestion" || b.kind === "busCongestion"
    )
    .filter(b => !kind || b.kind === kind);
}

function makeRouteId(departure: string, arrival: string, mode?: string) {
  return `route:${mode ?? "general"}:${departure}:${arrival}`;
}

function makeCongestionId(kind: string, name: string, line: string, direction?: string) {
  return `${kind}:${line}:${name}:${direction ?? ""}`;
}

export function isRouteBookmarked(departure: string, arrival: string, mode?: string): boolean {
  return loadAll().some(b => b.id === makeRouteId(departure, arrival, mode));
}

export function isCongestionBookmarked(
  kind: "subwayCongestion" | "busCongestion",
  name: string,
  line: string,
  direction?: string
): boolean {
  return loadAll().some(b => b.id === makeCongestionId(kind, name, line, direction));
}

/** 경로 북마크 켜기/끄기. 이미 있으면 삭제, 없으면 추가. 결과 상태(true=추가됨)를 반환. */
export function toggleRouteBookmark(route: {
  departure: string;
  arrival: string;
  estimatedTimeMin?: number;
  originalTimeMin?: number;
  mode?: "accessibility" | "general";
}): boolean {
  const id = makeRouteId(route.departure, route.arrival, route.mode);
  const list = loadAll();
  const idx = list.findIndex(b => b.id === id);
  if (idx >= 0) {
    list.splice(idx, 1);
    saveAll(list);
    return false;
  }
  list.push({
    id,
    kind: "route",
    departure: route.departure,
    arrival: route.arrival,
    estimatedTimeMin: route.estimatedTimeMin,
    originalTimeMin: route.originalTimeMin,
    mode: route.mode,
    savedAt: Date.now(),
  });
  saveAll(list);
  return true;
}

/** 혼잡도(지하철/버스) 북마크 켜기/끄기. */
export function toggleCongestionBookmark(item: {
  kind: "subwayCongestion" | "busCongestion";
  name: string;
  line: string;
  direction?: string;
}): boolean {
  const id = makeCongestionId(item.kind, item.name, item.line, item.direction);
  const list = loadAll();
  const idx = list.findIndex(b => b.id === id);
  if (idx >= 0) {
    list.splice(idx, 1);
    saveAll(list);
    return false;
  }
  list.push({
    id,
    kind: item.kind,
    name: item.name,
    line: item.line,
    direction: item.direction,
    savedAt: Date.now(),
  });
  saveAll(list);
  return true;
}

export function removeBookmark(id: string) {
  saveAll(loadAll().filter(b => b.id !== id));
}
