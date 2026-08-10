import { useState, useEffect, useRef } from "react";

const API_BASE = "https://subway-congestion-api.onrender.com";
const ROUTE_API_BASE = "http://127.0.0.1:5000";

declare global { interface Window { kakao: any; } }

import { BrowserRouter as Router, Routes, Route, useNavigate, useLocation } from "react-router";
import { Search, User, MapPin, Navigation, TrendingDown, Home, Map, X, Check, Train, Bus, Star } from "lucide-react";
import { ImageWithFallback } from "./components/figma/ImageWithFallback";

// ── 프로필 아바타 ────────────────────────────────────────────────
const AVATAR_OPTIONS = [
  { id: "avatar1", img: "PLACEHOLDER_AVATAR1", label: "강아지", bg: "bg-blue-100" },
  { id: "avatar2", img: "PLACEHOLDER_AVATAR2", label: "고양이", bg: "bg-gray-100" },
  { id: "avatar3", img: "PLACEHOLDER_AVATAR3", label: "병아리", bg: "bg-yellow-100" },
  { id: "avatar4", img: "PLACEHOLDER_AVATAR4", label: "토끼",  bg: "bg-red-100" },
  { id: "avatar5", img: "PLACEHOLDER_AVATAR5", label: "코알라", bg: "bg-gray-100" },
  { id: "avatar0", img: null as unknown as string, label: "기본", bg: "bg-gray-100" },
];

function getAvatar(avatarId?: string) {
  return AVATAR_OPTIONS.find(a => a.id === avatarId) ?? null;
}
// ────────────────────────────────────────────────────────────────

// ── 버스 여석/혼잡도 뱃지 ─────────────────────────────────────────
function CongestionBadge({ level }: { level: string }) {
  const styles: Record<string, string> = {
    "여유": "bg-green-100 text-green-700",
    "보통": "bg-yellow-100 text-yellow-700",
    "혼잡": "bg-red-100 text-red-700",
    "매우혼잡": "bg-red-200 text-red-800",
    "판단불가": "bg-gray-100 text-gray-500",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${styles[level] || styles["판단불가"]}`}>
      {level}
    </span>
  );
}
// ────────────────────────────────────────────────────────────────

// ── 검색 기록 관리 ──────────────────────────────────────────────
const HISTORY_KEY = "searchHistory";
const MAX_HISTORY = 10;

interface SearchRecord {
  departure: string;
  arrival: string;
  count: number;
  lastUsed: number;
}

const STATION_ALIASES: Record<string, string> = {
  "성신여대": "성신여자대학교",
  "성신여대입구": "성신여자대학교",
  "홍대": "홍익대학교",
  "홍대입구": "홍익대학교",
  "건대": "건국대학교",
  "건대입구": "건국대학교",
  "이대": "이화여자대학교",
  "이대입구": "이화여자대학교",
  "외대": "한국외국어대학교",
  "외대앞": "한국외국어대학교",
  "시립대": "서울시립대학교",
  "상암mbc": "상암MBC",
};

function normalizeName(raw: string): string {
  const noSpace = raw.replace(/\s+/g, "").toLowerCase();
  if (STATION_ALIASES[noSpace]) return STATION_ALIASES[noSpace];
  const noSuffix = noSpace.replace(/역$/, "");
  if (STATION_ALIASES[noSuffix]) return STATION_ALIASES[noSuffix];
  return noSuffix;
}

function isSameStation(a: string, b: string): boolean {
  return normalizeName(a) === normalizeName(b);
}

function loadHistory(): SearchRecord[] {
  try {
    return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]");
  } catch {
    return [];
  }
}

function recordSearch(departure: string, arrival: string) {
  const history = loadHistory();
  const existing = history.find(
    r => isSameStation(r.departure, departure) && isSameStation(r.arrival, arrival)
  );
  if (existing) {
    existing.count += 1;
    existing.lastUsed = Date.now();
    existing.departure = departure;
    existing.arrival = arrival;
  } else {
    history.push({ departure, arrival, count: 1, lastUsed: Date.now() });
  }
  const trimmed = history
    .sort((a, b) => b.lastUsed - a.lastUsed)
    .slice(0, MAX_HISTORY);
  sessionStorage.setItem(HISTORY_KEY, JSON.stringify(trimmed));
}

function getFrequentRoutes(): SearchRecord[] {
  return loadHistory()
    .filter(r => r.count >= 2)
    .sort((a, b) => b.count - a.count || b.lastUsed - a.lastUsed)
    .slice(0, 5);
}
// ────────────────────────────────────────────────────────────────

// ── 버스 북마크 관리 ────────────────────────────────────────────
const BUS_BOOKMARK_KEY = "busBookmarks";

function getBusBookmarkKey() {
  try {
    const user = JSON.parse(sessionStorage.getItem("loggedInUser") || "null");
    return user?.username ? `${BUS_BOOKMARK_KEY}_${user.username}` : BUS_BOOKMARK_KEY;
  } catch { return BUS_BOOKMARK_KEY; }
}

interface BusBookmark {
  routeId: string;
  routeNm: string;
  direction: string;
}

function loadBusBookmarks(): BusBookmark[] {
  try { return JSON.parse(localStorage.getItem(getBusBookmarkKey()) || "[]"); }
  catch { return []; }
}

function toggleBusBookmark(item: BusBookmark) {
  const list = loadBusBookmarks();
  const idx = list.findIndex(b => b.routeId === item.routeId && b.direction === item.direction);
  if (idx !== -1) list.splice(idx, 1);
  else list.push(item);
  localStorage.setItem(getBusBookmarkKey(), JSON.stringify(list));
  return list;
}

function isBusBookmarked(list: BusBookmark[], routeId: string, direction: string) {
  return list.some(b => b.routeId === routeId && b.direction === direction);
}
// ────────────────────────────────────────────────────────────────

function ProfileSelectScreen() {
  const navigate = useNavigate();
  const [selectedAvatar, setSelectedAvatar] = useState<string | null>(null);

  const handleConfirm = () => {
    if (!selectedAvatar) return;
    // 저장: loggedInUser + users 배열 모두 업데이트
    try {
      const user = JSON.parse(sessionStorage.getItem("loggedInUser") || "null");
      if (user) {
        const updated = { ...user, avatarId: selectedAvatar };
        sessionStorage.setItem("loggedInUser", JSON.stringify(updated));
        const users: any[] = JSON.parse(localStorage.getItem("users") || "[]");
        const idx = users.findIndex((u: any) => u.username === user.username);
        if (idx !== -1) users[idx] = updated;
        localStorage.setItem("users", JSON.stringify(users));
      }
    } catch {}
    navigate("/account");
  };

  return (
    <div className="size-full bg-gray-50 flex flex-col">
      <div className="bg-white border-b border-gray-200 p-4">
        <h1 className="text-xl font-bold text-gray-800">프로필 사진 선택</h1>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center p-8 space-y-8">
        <div className="text-center">
          <p className="text-2xl font-bold text-gray-800 mb-2">환영해요! 🎉</p>
          <p className="text-gray-500 text-sm">사용할 프로필 사진을 선택해주세요</p>
        </div>

        <div className="grid grid-cols-3 gap-4 w-full max-w-xs">
          {["avatar4", "avatar1", "avatar3", "avatar5", "avatar2", "avatar0"].map(id => {
            const avatar = AVATAR_OPTIONS.find(a => a.id === id);
            if (!avatar) return null;
            return (
              <button
                key={avatar.id}
                onClick={() => setSelectedAvatar(avatar.id)}
                className={`flex flex-col items-center gap-2 p-3 rounded-2xl border-2 transition-all ${
                  selectedAvatar === avatar.id
                    ? "border-blue-500 shadow-lg scale-105 bg-blue-50"
                    : "border-gray-200 bg-white hover:border-gray-300"
                }`}
              >
                <div className="w-14 h-14 rounded-full overflow-hidden flex items-center justify-center bg-gray-100">
                  {avatar.img ? (
                    <img src={avatar.img} alt={avatar.label} className="w-full h-full object-cover" />
                  ) : (
                    <User className="w-8 h-8 text-gray-400" />
                  )}
                </div>
                <span className="text-xs font-semibold text-gray-700">{avatar.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="bg-white border-t border-gray-200 p-4">
        <button
          onClick={handleConfirm}
          disabled={!selectedAvatar}
          className="w-full bg-blue-600 text-white py-3 rounded-xl font-semibold disabled:bg-gray-300 disabled:cursor-not-allowed hover:bg-blue-700 transition-colors"
        >
          선택 완료
        </button>
      </div>
    </div>
  );
}

function SplashScreen({ onComplete }: { onComplete: () => void }) {
  useEffect(() => {
    const timer = setTimeout(() => { onComplete(); }, 2000);
    return () => clearTimeout(timer);
  }, [onComplete]);

  return (
    <div className="size-full relative flex items-center justify-center overflow-hidden">
      <ImageWithFallback
        src="https://images.unsplash.com/photo-1763462929966-23955f0a8a2d?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=1080"
        alt="지도 배경"
        className="absolute inset-0 w-full h-full object-cover"
      />
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative z-10 text-center">
        <h1 className="text-6xl font-bold text-white mb-4">여유로</h1>
        <p className="text-xl text-white/90">혼잡도를 고려한 스마트 경로 안내</p>
      </div>
    </div>
  );
}

// ── 카카오 좌표 → 주소 변환 ──────────────────────────────────────
// ── 백엔드 프록시를 통한 좌표 → 주소 변환 ──────────────────────
async function coordToAddress(lat: number, lng: number): Promise<string> {
  const KAKAO_KEY = "6c220101133197233daf87a3ec931801";

  const res = await fetch(
    `https://dapi.kakao.com/v2/local/geo/coord2address.json?x=${lng}&y=${lat}`,
    { headers: { Authorization: `KakaoAK ${KAKAO_KEY}` } }
  );

  if (!res.ok) throw new Error("API 호출 실패");

  const data = await res.json();
  const doc = data.documents?.[0];

  // 지번주소: region 필드로 동(洞) 단위까지만 조합
  // (address_name 전체를 쓰면 "213-1" 같은 번지가 포함돼 백엔드 검색이 실패함)
  const addr = doc?.address;
  if (addr) {
    const parts = [
      addr.region_1depth_name, // 예: 서울
      addr.region_2depth_name, // 예: 성북구
      addr.region_3depth_name, // 예: 동선동3가
    ].filter(Boolean);
    if (parts.length > 0) return parts.join(" ");
  }

  // 도로명 주소: 끝의 건물번호(숫자 또는 숫자-숫자) 제거
  const roadName = doc?.road_address?.address_name;
  if (roadName) {
    return roadName.replace(/\s+\d+(-\d+)?$/, "").trim();
  }

  return "";
}
// ────────────────────────────────────────────────────────────────
// ────────────────────────────────────────────────────────────────

function SearchModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [departure, setDeparture] = useState("");
  const [arrival, setArrival] = useState("");
  const [isLocating, setIsLocating] = useState(false);
  const [isElderlySelected, setIsElderlySelected] = useState(false);
  const [isPregnantSelected, setIsPregnantSelected] = useState(false);

  // 하나라도 선택되면 교통약자 모드로 판단
  const isAccessibilityMode = isElderlySelected || isPregnantSelected;

  const handleGPS = () => {
    if (!navigator.geolocation) {
      alert("GPS를 지원하지 않는 브라우저입니다.");
      return;
    }
    setIsLocating(true);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          const address = await coordToAddress(pos.coords.latitude, pos.coords.longitude);
          setDeparture(address || `${pos.coords.latitude.toFixed(5)}, ${pos.coords.longitude.toFixed(5)}`);
        } catch {
          alert("주소 변환에 실패했습니다.");
        } finally {
          setIsLocating(false);
        }
      },
      () => {
        alert("위치 권한을 허용해주세요.");
        setIsLocating(false);
      },
      { enableHighAccuracy: true, timeout: 10000 }
    );
  };

  const handleSearch = () => {
    if (departure && arrival) {
      recordSearch(departure, arrival);
      onClose();
      navigate("/routes", {
        state: {
          departure,
          arrival,
          isAccessibilityMode,
          accessibilityType: isElderlySelected && isPregnantSelected
            ? "both"
            : isElderlySelected
            ? "elderly"
            : isPregnantSelected
            ? "pregnant"
            : null,
        },
      });
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-end justify-center z-50">
      <div className="bg-white rounded-t-3xl w-full p-6 space-y-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-xl font-bold text-gray-800">경로 검색</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-6 h-6" />
          </button>
        </div>
        <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-xl">
          <MapPin className="w-5 h-5 text-blue-600 flex-shrink-0" />
          <input
            type="text"
            placeholder="출발지 입력"
            value={departure}
            onChange={(e) => setDeparture(e.target.value)}
            className="flex-1 bg-transparent outline-none text-gray-800 placeholder-gray-400"
            autoFocus
          />
          <button
            onClick={handleGPS}
            disabled={isLocating}
            className="flex-shrink-0 text-blue-500 hover:text-blue-700 disabled:text-gray-300 text-lg"
            title="현재 위치 가져오기"
          >
            {isLocating ? "⏳" : "📍"}
          </button>
        </div>
        <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-xl">
          <Navigation className="w-5 h-5 text-red-600 flex-shrink-0" />
          <input
            type="text"
            placeholder="도착지 입력"
            value={arrival}
            onChange={(e) => setArrival(e.target.value)}
            className="flex-1 bg-transparent outline-none text-gray-800 placeholder-gray-400"
          />
        </div>

        {/* 교통약자 아이콘 선택 */}
        <div>
          <p className="text-sm font-semibold text-gray-700 mb-2">교통약자를 위한 경로가 필요하신가요?</p>
          <div className="flex gap-3">
            <button
              onClick={() => setIsElderlySelected(prev => !prev)}
              className={`flex-1 flex flex-col items-center gap-1.5 py-3 rounded-xl border-2 transition-colors ${
                isElderlySelected
                  ? "border-yellow-400 bg-yellow-50"
                  : "border-gray-200 bg-gray-50 hover:border-gray-300"
              }`}
            >
              <span
                className={`text-3xl transition-all ${
                  isElderlySelected ? "" : "grayscale opacity-50"
                }`}
              >
                👴
              </span>
              <span
                className={`text-xs font-semibold ${
                  isElderlySelected ? "text-yellow-700" : "text-gray-500"
                }`}
              >
                노약자
              </span>
            </button>

            <button
              onClick={() => setIsPregnantSelected(prev => !prev)}
              className={`flex-1 flex flex-col items-center gap-1.5 py-3 rounded-xl border-2 transition-colors ${
                isPregnantSelected
                  ? "border-pink-400 bg-pink-50"
                  : "border-gray-200 bg-gray-50 hover:border-gray-300"
              }`}
            >
              <span
                className={`text-3xl transition-all ${
                  isPregnantSelected ? "" : "grayscale opacity-50"
                }`}
              >
                🤰
              </span>
              <span
                className={`text-xs font-semibold ${
                  isPregnantSelected ? "text-pink-700" : "text-gray-500"
                }`}
              >
                임산부
              </span>
            </button>
          </div>
        </div>

        <button
          onClick={handleSearch}
          disabled={!departure || !arrival}
          className="w-full bg-blue-600 text-white py-3 rounded-xl font-semibold disabled:bg-gray-300 disabled:cursor-not-allowed hover:bg-blue-700 transition-colors"
        >
          <Search className="w-5 h-5 inline-block mr-2" />
          경로 검색
        </button>
      </div>
    </div>
  );
}


function MainScreen() {
  const location = useLocation();
  const [activeTab, setActiveTab] = useState<"myTransit" | "congestion">("myTransit");
  const [showSearchModal, setShowSearchModal] = useState(false);
  const [currentTipIdx, setCurrentTipIdx] = useState(0);
  const [tipEnabled, setTipEnabled] = useState<boolean>(true);

  // 홈 화면으로 돌아올 때마다 재동기화
  // 비로그인이거나 저장값 없으면 항상 true(표시)
  useEffect(() => {
    const key = getTipKey();
    if (!key) { setTipEnabled(true); return; }
    const stored = localStorage.getItem(key);
    setTipEnabled(stored === null ? true : stored === "true");
  }, [location]);

  const tips = [
    { type: "팁", emoji: "💡", text: "출퇴근 시간대엔 전 정거장 탑승으로 자리를 확보하세요" },
    { type: "팁", emoji: "🚌", text: "광역버스 혼잡도는 실시간으로 반영됩니다" },
    { type: "팁", emoji: "⏱️", text: "혼잡도 반영 경로는 기본 경로보다 쾌적하게 이동할 수 있어요" },
  ];

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTipIdx(prev => (prev + 1) % tips.length);
    }, 3500);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="size-full relative flex flex-col overflow-hidden">
      <ImageWithFallback
        src="https://images.unsplash.com/photo-1767873691315-c9e61c705b25?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=1080"
        alt="지도 배경"
        className="absolute inset-0 w-full h-full object-cover opacity-20"
      />

      <div className="relative z-10 flex-1 flex flex-col overflow-hidden">
        <div className="bg-white border-b border-gray-200 p-3 flex gap-2 overflow-x-auto">
          <button
            onClick={() => setActiveTab("myTransit")}
            className={`flex-shrink-0 px-4 py-2 rounded-lg font-semibold transition-colors ${
              activeTab === "myTransit" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"
            }`}
          >
            마이교통
          </button>
          <button
            onClick={() => setActiveTab("congestion")}
            className={`flex-shrink-0 px-4 py-2 rounded-lg font-semibold transition-colors ${
              activeTab === "congestion" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"
            }`}
          >
            실시간혼잡도
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {tipEnabled && (
          <div className="px-4 pt-4">
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
              {tips.map((tip, idx) => (
                <div
                  key={idx}
                  className={`flex items-center gap-3 px-4 py-3 transition-all duration-500 ${
                    idx === currentTipIdx ? "block" : "hidden"
                  }`}
                >
                  <span className="text-xl flex-shrink-0">{tip.emoji}</span>
                  <div className="flex-1 min-w-0">
                    <span className={`text-xs font-bold px-2 py-0.5 rounded-full mr-2 ${
                      tip.type === "공지" ? "bg-red-100 text-red-600" : "bg-blue-100 text-blue-600"
                    }`}>
                      {tip.type}
                    </span>
                    <span className="text-sm text-gray-700">{tip.text}</span>
                  </div>
                </div>
              ))}
              <div className="flex justify-center gap-1.5 pb-2">
                {tips.map((_, idx) => (
                  <button
                    key={idx}
                    onClick={() => setCurrentTipIdx(idx)}
                    className={`h-1.5 rounded-full transition-all ${
                      idx === currentTipIdx ? "bg-blue-600 w-3" : "bg-gray-300 w-1.5"
                    }`}
                  />
                ))}
              </div>
            </div>
          </div>
          )}

          <div className="pt-3">
            {activeTab === "myTransit" && <MyTransitTab />}
            {activeTab === "congestion" && <CongestionTab />}
          </div>
        </div>
      </div>

      {showSearchModal && <SearchModal onClose={() => setShowSearchModal(false)} />}
      <BottomNavigation onSearchClick={() => setShowSearchModal(true)} />
    </div>
  );
}

function MyTransitTab() {
  const navigate = useNavigate();
  const [frequentRoutes, setFrequentRoutes] = useState<ReturnType<typeof getFrequentRoutes>>([]);

  useEffect(() => {
    setFrequentRoutes(getFrequentRoutes());
  }, []);

  const [busBookmarks, setBusBookmarksState] = useState<BusBookmark[]>([]);
  const [busCongestionMap, setBusCongestionMap] = useState<Record<string, any>>({});

  useEffect(() => {
    const list = loadBusBookmarks();
    setBusBookmarksState(list);
    list.forEach(b => {
      fetch(`${ROUTE_API_BASE}/bus/search?routeNm=${encodeURIComponent(b.routeNm)}`)
        .then(res => res.json())
        .then(data => {
          const matched = (data.routes || []).find((r: any) => r.routeId === b.routeId && r.direction === b.direction);
          if (matched) setBusCongestionMap(prev => ({ ...prev, [`${b.routeId}-${b.direction}`]: matched }));
        })
        .catch(() => {});
    });
  }, []);

  const handleRouteClick = (departure: string, arrival: string) => {
    recordSearch(departure, arrival);
    navigate("/routes", { state: { departure, arrival } });
  };

  return (
    <div className="p-4 space-y-4">
      <h2 className="text-xl font-bold text-gray-800">자주 가는 경로</h2>
      {frequentRoutes.length === 0 ? (
        <div className="bg-white rounded-xl p-8 shadow-md text-center text-gray-400">
          <MapPin className="w-10 h-10 mx-auto mb-3 text-gray-300" />
          <p className="font-medium">아직 이용한 경로가 없어요</p>
          <p className="text-sm mt-1">경로를 검색하면 여기에 표시됩니다</p>
        </div>
      ) : (
        <div className="space-y-3">
          {frequentRoutes.map((route, idx) => (
            <button
              key={idx}
              onClick={() => handleRouteClick(route.departure, route.arrival)}
              className="w-full bg-white rounded-xl p-4 shadow-md flex items-center gap-4 hover:bg-blue-50 transition-colors text-left"
            >
              <div className="flex flex-col items-center gap-1 flex-shrink-0">
                <div className="w-2.5 h-2.5 rounded-full bg-blue-500" />
                <div className="w-0.5 h-5 bg-gray-300" />
                <div className="w-2.5 h-2.5 rounded-full bg-red-500" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-semibold text-gray-800 truncate">{route.departure}</div>
                <div className="font-semibold text-gray-800 truncate">{route.arrival}</div>
              </div>
              <div className="flex-shrink-0">
                <span className="text-xs bg-blue-100 text-blue-700 font-semibold px-2 py-1 rounded-full">
                  {route.count}회
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
      {busBookmarks.length > 0 && (
        <div className="pt-2">
          <h2 className="text-xl font-bold text-gray-800 mb-3">즐겨찾는 버스</h2>
          <div className="space-y-3">
            {busBookmarks.map((b) => {
              const route = busCongestionMap[`${b.routeId}-${b.direction}`];
              const levels = route ? route.stations.map((s: any) => Number(s.congestionLevel)) : [];
              const maxLevel = levels.length ? Math.max(...levels) : 0;
              const cg = maxLevel === 5 ? { badge: "bg-red-100 text-red-700", label: "혼잡" }
                : maxLevel === 4 ? { badge: "bg-yellow-100 text-yellow-700", label: "보통" }
                : maxLevel === 3 ? { badge: "bg-green-100 text-green-700", label: "여유" }
                : { badge: "bg-gray-100 text-gray-500", label: "정보없음" };
              return (
                <div key={`${b.routeId}-${b.direction}`} className="bg-white rounded-xl p-4 shadow-md flex items-center gap-4">
                  <Bus className="w-6 h-6 text-blue-600 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-gray-800">{b.routeNm}번</div>
                    <div className="text-sm text-gray-500">{b.direction}</div>
                  </div>
                  <span className={`px-2 py-0.5 rounded-md text-xs font-semibold ${cg.badge}`}>{cg.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function CongestionTab() {
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedLine, setSelectedLine] = useState("");
  const [allData, setAllData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busQuery, setBusQuery] = useState("");
  const [busResults, setBusResults] = useState<any[]>([]);
  const [busSource, setBusSource] = useState<"gbis" | "stats">("gbis");
  const [busLoading, setBusLoading] = useState(false);
  const [busError, setBusError] = useState<string | null>(null);
  const [bookmarks, setBookmarks] = useState<BusBookmark[]>(() => loadBusBookmarks());
  const [selectedBusCity, setSelectedBusCity] = useState("");

  // 버스 검색 결과가 하나라도 있으면 "버스 검색 모드"로 간주 -> 지하철 그리드 숨김
  const isBusSearchActive = busResults.length > 0 || busLoading || !!busError;

  const handleBusSearch = () => {
    if (!busQuery) return;
    setBusLoading(true);
    setBusError(null);

    const isSeoul = selectedBusCity === "seoul";

    const url = isSeoul
      ? `${ROUTE_API_BASE}/api/congestion/route?routeNm=${encodeURIComponent(busQuery)}`
      : `${ROUTE_API_BASE}/bus/search?routeNm=${encodeURIComponent(busQuery)}${
          selectedBusCity ? `&cityCode=${selectedBusCity}` : ""
        }`;

    fetch(url)
      .then(res => res.json())
      .then(data => {
        if (!data.routes || data.routes.length === 0) {
          setBusError(data.error || "검색 결과가 없습니다.");
          setBusResults([]);
        } else {
          setBusResults(data.routes);
          setBusSource(isSeoul ? "stats" : "gbis");
        }
        setBusLoading(false);
      })
      .catch(() => {
        setBusError("버스 정보를 불러오지 못했습니다.");
        setBusLoading(false);
      });
  };

  // 버스 검색창을 비우면 다시 지하철 그리드가 보이도록 리셋
  const handleBusQueryChange = (value: string) => {
    setBusQuery(value);
    if (!value) {
      setBusResults([]);
      setBusError(null);
    }
  };

  const now = new Date();
  const currentHour = now.getHours();
  const currentMinutes = now.getMinutes() >= 30 ? "30분" : "00분";
  const currentTimeLabel = `${currentHour}시${currentMinutes}`;
  const dayOfWeek = now.getDay();
  const dayType = dayOfWeek === 0 ? "일요일" : dayOfWeek === 6 ? "토요일" : "평일";
  const lines = ["1호선", "2호선", "3호선", "4호선", "5호선", "6호선", "7호선", "8호선"];
  const BUS_CITIES = [
    { code: "seoul", name: "서울" },
    { code: "31020", name: "성남" },
    { code: "31010", name: "수원" },
    { code: "31190", name: "용인" },
    { code: "31100", name: "고양" },
    { code: "31050", name: "부천" },
    { code: "31040", name: "안양" },
    { code: "21", name: "부산" },
    { code: "23", name: "인천" },
    { code: "22", name: "대구" },
  ];

  useEffect(() => {
    // 버스 검색 중일 땐 지하철 데이터가 필요 없으니 재조회하지 않음
    if (isBusSearchActive) return;

    fetch(`${API_BASE}/congestion`)
      .then(res => {
        if (!res.ok) throw new Error("데이터를 불러오지 못했습니다.");
        return res.json();
      })
      .then(data => {
        const list = Array.isArray(data) ? data : data.data ?? [];
        setAllData(list);
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = allData.filter(item => {
    const matchTime = item["시간대"] === currentTimeLabel;
    const matchDay = item["요일구분"] === dayType;
    const matchLine = !selectedLine || item["호선"] === selectedLine;
    const matchStation = !searchQuery || item["출발역"]?.includes(searchQuery.replace("역", ""));
    return matchTime && matchDay && matchLine && matchStation;
  });

  const getCongestionColor = (value: number) => {
    if (value >= 80) return { badge: "bg-red-100 text-red-700", bar: "bg-red-500", label: "혼잡" };
    if (value >= 30) return { badge: "bg-yellow-100 text-yellow-700", bar: "bg-yellow-500", label: "보통" };
    return { badge: "bg-green-100 text-green-700", bar: "bg-green-500", label: "쾌적" };
  };

  const getStatsLevelStyle = (level: string) => {
    if (level === "혼잡") return "bg-red-100 text-red-700";
    if (level === "보통") return "bg-yellow-100 text-yellow-700";
    return "bg-green-100 text-green-700";
  };

  const getStatsBarColor = (level: string) => {
    if (level === "혼잡") return "bg-red-500";
    if (level === "보통") return "bg-yellow-500";
    return "bg-green-500";
  };

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-gray-800">실시간 혼잡도</h2>
        <span className="text-sm text-gray-500">{currentTimeLabel} ({dayType})</span>
      </div>

      {/* 지하철 노선 필터 (검색창보다 위) */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1">
        <div className="flex-shrink-0 pl-1 pr-1">
          <Train className="w-5 h-5 text-gray-400" />
        </div>
        <button
          onClick={() => setSelectedLine("")}
          className={`flex-shrink-0 px-3 py-1.5 rounded-full text-sm font-semibold transition-colors ${selectedLine === "" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600"}`}
        >
          전체
        </button>
        {lines.map((line, idx) => (
          <button
            key={idx}
            onClick={() => setSelectedLine(line)}
            className={`flex-shrink-0 px-3 py-1.5 rounded-full text-sm font-semibold transition-colors ${selectedLine === line ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600"}`}
          >
            {line}
          </button>
        ))}
      </div>

      {/* 역 이름 검색창: 지하철 필터 바로 아래로 이동 */}
      <div className="bg-white rounded-xl p-3 shadow-md flex items-center gap-2">
        <Search className="w-5 h-5 text-gray-400" />
        <input
          type="text"
          placeholder="역 이름 검색 (예: 강남)"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="flex-1 outline-none text-gray-800 placeholder-gray-400"
        />
      </div>

      {/* 버스 혼잡도 검색 */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 mt-2">
        <div className="flex-shrink-0 pl-1 pr-1">
          <Bus className="w-5 h-5 text-gray-400" />
        </div>
        <button
          onClick={() => setSelectedBusCity("")}
          className={`flex-shrink-0 px-3 py-1.5 rounded-full text-sm font-semibold transition-colors ${selectedBusCity === "" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600"}`}
        >
          전체
        </button>
        {BUS_CITIES.map((city) => (
          <button
            key={city.code}
            onClick={() => setSelectedBusCity(city.code)}
            className={`flex-shrink-0 px-3 py-1.5 rounded-full text-sm font-semibold transition-colors ${selectedBusCity === city.code ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600"}`}
          >
            {city.name}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2 mt-2">
        <input
          type="text"
          placeholder="버스 번호 입력 (예: 140)"
          value={busQuery}
          onChange={(e) => handleBusQueryChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleBusSearch(); }}
          className="flex-1 px-3 py-2 bg-white border border-gray-200 rounded-lg outline-none focus:border-blue-400 text-sm"
        />
        <button
          onClick={handleBusSearch}
          disabled={!busQuery || busLoading}
          className="px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-semibold disabled:bg-gray-300"
        >
          검색
        </button>
      </div>

      {busLoading && <div className="text-center py-4 text-gray-400 text-sm">버스 정보 불러오는 중...</div>}
      {busError && <div className="text-center py-4 text-red-500 text-sm">{busError}</div>}

      {/* 서울(통계 기반) 검색 결과 */}
      {busResults.length > 0 && busSource === "stats" && (
        <div className="space-y-3">
          {busResults.map((route) => {
            // "142번(도봉동~고속터미널)" -> "도봉동~고속터미널" (괄호 안 내용만 추출)
            const routeNameMatch = (route.routeName || "").match(/\((.+)\)/);
            const routeNameShort = routeNameMatch ? routeNameMatch[1] : route.routeName;
            return (
              <div key={route.routeNm} className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="font-bold text-gray-800">{route.routeNm}번</span>
                    <span className="text-sm text-gray-500 ml-2">{routeNameShort}</span>
                  </div>
                </div>
                <p className="text-xs text-gray-400">경유 정류소 {route.stations.length}개</p>

                <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
                  {route.stations.map((s: any) => (
                    <div key={s.stopId} className="border border-gray-100 rounded-lg p-2.5">
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-sm font-semibold text-gray-800 truncate">{s.stationName}</span>
                        <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold flex-shrink-0 ml-2 ${getStatsLevelStyle(s.level)}`}>
                          {s.level}
                        </span>
                      </div>
                      <div className="bg-gray-100 rounded-full h-1.5">
                        <div
                          className={`h-1.5 rounded-full ${getStatsBarColor(s.level)}`}
                          style={{ width: `${s.barPercent}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 그 외 도시(실시간 GBIS) 검색 결과 */}
      {busResults.length > 0 && busSource === "gbis" && (
        <div className="space-y-3">
          {busResults.map((route) => {
            const bookmarked = isBusBookmarked(bookmarks, route.routeId, route.direction);
            const levels = route.stations.map((s: any) => Number(s.congestionLevel));
            const maxLevel = levels.length ? Math.max(...levels) : 0;
            const cg = maxLevel === 5 ? { badge: "bg-red-100 text-red-700", label: "혼잡" }
              : maxLevel === 4 ? { badge: "bg-yellow-100 text-yellow-700", label: "보통" }
              : maxLevel === 3 ? { badge: "bg-green-100 text-green-700", label: "여유" }
              : { badge: "bg-gray-100 text-gray-500", label: "정보없음" };
            return (
              <div key={`${route.routeId}-${route.direction}`} className="bg-white rounded-xl p-4 shadow-sm border border-gray-100">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="font-bold text-gray-800">{route.routeNm}번</span>
                    <span className="text-sm text-gray-500 ml-2">{route.direction}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded-md text-xs font-semibold ${cg.badge}`}>{cg.label}</span>
                    <button
                      onClick={() => setBookmarks(toggleBusBookmark({ routeId: route.routeId, routeNm: route.routeNm, direction: route.direction }))}
                    >
                      <Star className={`w-5 h-5 ${bookmarked ? "fill-yellow-400 text-yellow-400" : "text-gray-300"}`} />
                    </button>
                  </div>
                </div>
                <p className="text-xs text-gray-400 mt-1">정류소 {route.stations.length}개 구간</p>
              </div>
            );
          })}
        </div>
      )}

      {/* 버스 검색이 활성화되지 않았을 때만 지하철 그리드 표시 */}
      {!isBusSearchActive && (
        <>
          <p className="text-xs text-gray-400">현재 시간({currentTimeLabel}, {dayType}) 기준 혼잡도입니다</p>

          {loading && <div className="text-center py-10 text-gray-500">혼잡도 데이터 불러오는 중...</div>}
          {error && <div className="text-center py-10 text-red-500">{error}</div>}

          {!loading && !error && (
            <div className="grid grid-cols-2 gap-3">
              {filtered.length > 0 ? filtered.map((item: any, idx: number) => {
                const percentage = Number(item["혼잡도"] ?? 0);
                const congestion = getCongestionColor(percentage);
                const displayPercent = Math.min(Math.round(percentage), 100);
                return (
                  <div key={idx} className="bg-white rounded-xl p-3 shadow-sm flex flex-col justify-between border border-gray-100">
                    <div className="flex flex-col mb-2 gap-1">
                      <div className="flex items-start justify-between gap-1">
                        <span className="font-bold text-gray-800 text-base truncate">{item["출발역"]}</span>
                        <span className={`px-2 py-0.5 rounded-md text-[10px] whitespace-nowrap font-semibold flex-shrink-0 ${congestion.badge}`}>
                          {congestion.label}
                        </span>
                      </div>
                      <span className="text-xs text-gray-500 truncate">{item["호선"]} {item["상하구분"]}</span>
                    </div>
                    <div className="mt-auto pt-2">
                      <div className="w-full bg-gray-100 rounded-full h-2">
                        <div className={`h-2 rounded-full ${congestion.bar}`} style={{ width: `${displayPercent}%` }} />
                      </div>
                    </div>
                  </div>
                );
              }) : (
                <div className="col-span-2 text-center py-10 text-gray-400 text-sm">
                  {searchQuery ? `"${searchQuery}" 검색 결과가 없습니다.` : "현재 시간대 데이터가 없습니다."}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}




function BottomNavigation({ onSearchClick }: { onSearchClick?: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const isActive = (path: string) => location.pathname === path;

  return (
    <div className="relative z-10 bg-white border-t border-gray-200 flex items-center justify-around px-4 pt-3 pb-4">
      <button
        onClick={() => navigate("/")}
        className={`flex flex-col items-center gap-1 transition-colors ${isActive("/") ? "text-blue-600" : "text-gray-600 hover:text-blue-600"}`}
      >
        <Home className="w-6 h-6" />
        <span className="text-xs">홈</span>
      </button>
      <button
        onClick={onSearchClick ?? (() => navigate("/"))}
        className="-mt-6 bg-blue-600 text-white w-14 h-14 rounded-full shadow-lg flex items-center justify-center hover:bg-blue-700 transition-colors"
      >
        <Search className="w-6 h-6" />
      </button>
      <button
        onClick={() => navigate("/account")}
        className={`flex flex-col items-center gap-1 transition-colors ${isActive("/account") ? "text-blue-600" : "text-gray-600 hover:text-blue-600"}`}
      >
        <User className="w-6 h-6" />
        <span className="text-xs">계정</span>
      </button>
    </div>
  );
}

type RouteFetchParams = {
  departure: string;
  arrival: string;
  currentHour: number;
  currentMinute: number;
  currentWeekday: number;
};

// ══════════════════════════════════════════════════════════════
// 🟡 교통약자 경로 생성 — [교통약자팀 담당]
// 노약자/임산부 아이콘을 선택하고 검색했을 때 호출되는 함수입니다.
// 이 함수 안의 fetch URL과 파라미터를 여기서 수정하세요.
// ══════════════════════════════════════════
function fetchAccessibilityRoutes({
  departure, arrival, currentHour, currentMinute, currentWeekday,
}: RouteFetchParams) {
  return fetch(
    `${ROUTE_API_BASE}/api/routes?start=${encodeURIComponent(departure)}&end=${encodeURIComponent(arrival)}&hour=${currentHour}&minute=${currentMinute}&weekday=${currentWeekday}`
  ).then(res => {
    if (!res.ok) throw new Error("경로를 불러오지 못했습니다.");
    return res.json();
  });
}

// ══════════════════════════════════════════════════════════════
// 🔵 일반인 경로 생성 — [일반경로팀 담당]
// 아이콘을 선택하지 않고 검색했을 때 호출되는 함수
// ══════════════════════════════
function fetchGeneralRoutes({
  departure, arrival, currentHour, currentMinute, currentWeekday,
}: RouteFetchParams) {
  return fetch(
    `${ROUTE_API_BASE}/api/routes?start=${encodeURIComponent(departure)}&end=${encodeURIComponent(arrival)}&hour=${currentHour}&minute=${currentMinute}&weekday=${currentWeekday}&mode=general`
  ).then(res => {
    if (!res.ok) throw new Error("경로를 불러오지 못했습니다.");
    return res.json();
  });
}

function RouteResultScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const { departure, arrival, isAccessibilityMode, accessibilityType } =
  (location.state as {
    departure: string;
    arrival: string;
    isAccessibilityMode?: boolean;
    accessibilityType?: string | null;
  }) || {};

  const [routes, setRoutes] = useState<any[]>([]);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedIdx, setSelectedIdx] = useState(0);

   useEffect(() => {
    if (!departure || !arrival) return;

    const now = new Date();
    const currentHour = now.getHours();
    const currentMinute = now.getMinutes();
    const jsDay = now.getDay();
    const currentWeekday = jsDay === 0 ? 6 : jsDay - 1;

    const params = { departure, arrival, currentHour, currentMinute, currentWeekday };

    const fetchPromise = isAccessibilityMode
      ? fetchAccessibilityRoutes(params)
      : fetchGeneralRoutes(params);

    fetchPromise
      .then(result => {
        if (result.status === "fail") {
          throw new Error(result.message || "위치를 지도에서 찾을 수 없습니다.");
        }
        setData(result);
        const list = Array.isArray(result) ? result : result.routes ?? [];
        setRoutes(list.slice(0, 10));
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
  }, [departure, arrival, isAccessibilityMode]);

  const currentRoute = routes[selectedIdx];
  const isRushHour = data?.is_rush_hour;

  const getRouteLabel = (idx: number) => {
    if (isRushHour && idx < 3) return `AI 러시아워 ${idx + 1}`;
    const generalIdx = isRushHour ? idx - 3 + 1 : idx + 1;
    return `일반 경로 ${generalIdx}`;
  };

  const getTimeDiff = (route: any) => {
    if (!route) return null;
    const diff = route.estimated_comfort_time_min - route.original_time_min;
    if (diff === 0) return null;
    return diff > 0 ? `+${diff}분` : `${diff}분`;
  };

  return (
    <div className="size-full flex flex-col bg-gray-50">
      <div className="bg-white border-b border-gray-200 p-4">
        <button onClick={() => navigate(-1)} className="text-blue-600 font-semibold mb-3">
          ← 돌아가기
        </button>
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-gray-700">
            <MapPin className="w-4 h-4 text-blue-600" />
            <span className="font-medium">{departure}</span>
          </div>
          <div className="flex items-center gap-2 text-gray-700">
            <Navigation className="w-4 h-4 text-red-600" />
            <span className="font-medium">{arrival}</span>
          </div>
        </div>
      </div>

      {loading && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center space-y-3">
            <div className="w-10 h-10 border-4 border-blue-600 border-t-transparent rounded-full animate-spin mx-auto" />
            <p className="text-gray-500">경로를 불러오는 중...</p>
          </div>
        </div>
      )}

      {error && (
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="text-center space-y-3">
            <p className="text-red-500 font-semibold">{error}</p>
            <button onClick={() => navigate(-1)} className="px-6 py-2 bg-blue-600 text-white rounded-xl font-semibold">
              돌아가기
            </button>
          </div>
        </div>
      )}

      {!loading && !error && routes.length > 0 && currentRoute && (
        <>
          <div className="flex gap-2 p-3 bg-white border-b border-gray-200 overflow-x-auto">
            {routes.map((route, idx) => {
              const isAI = isRushHour && idx < 3;
              const isSelected = selectedIdx === idx;
              return (
                <button
                  key={idx}
                  onClick={() => setSelectedIdx(idx)}
                  className={`flex-shrink-0 px-4 py-3 rounded-xl border-2 transition-all ${
                    isSelected
                      ? isAI ? "bg-orange-500 text-white border-orange-500" : "bg-blue-600 text-white border-blue-600"
                      : isAI ? "bg-orange-50 text-orange-700 border-orange-300 hover:border-orange-400" : "bg-white text-gray-700 border-gray-200 hover:border-gray-300"
                  }`}
                >
                  <div className="text-xs font-bold mb-1">{isAI ? "🤖 " : ""}{getRouteLabel(idx)}</div>
                  <div className="text-sm opacity-90">{route.estimated_comfort_time_min}분</div>
                </button>
              );
            })}
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <div className={`bg-white rounded-xl p-4 shadow-md border-2 ${isRushHour && selectedIdx < 3 ? "border-orange-300" : "border-blue-200"}`}>
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-bold text-lg text-gray-800">{getRouteLabel(selectedIdx)}</h3>
                {isRushHour && selectedIdx < 3 && (
                  <span className="px-3 py-1 bg-orange-100 text-orange-600 rounded-full text-xs font-bold">🤖 AI 추천</span>
                )}
              </div>

              <div className="grid grid-cols-3 gap-4 text-center mb-4">
                <div>
                  <div className="text-2xl font-bold text-blue-600">{currentRoute.estimated_comfort_time_min}분</div>
                  <div className="text-xs text-gray-500">예상 소요시간</div>
                </div>
                <div>
                  <div className="text-2xl font-bold text-gray-700">{currentRoute.original_time_min}분</div>
                  <div className="text-xs text-gray-500">기본 소요시간</div>
                </div>
                <div>
                  <div className="text-2xl font-bold text-gray-700">{currentRoute.payment_krw?.toLocaleString()}원</div>
                  <div className="text-xs text-gray-500">요금</div>
                </div>
              </div>

              {getTimeDiff(currentRoute) && (
                <div className="bg-blue-50 rounded-lg px-3 py-2 text-sm text-blue-700 text-center mb-4">
                  기본 경로 대비 {getTimeDiff(currentRoute)} 소요
                </div>
              )}

              <div className="border-t border-gray-200 pt-4 space-y-4">
                {currentRoute.sub_paths && currentRoute.sub_paths.length > 0 ? (
                  currentRoute.sub_paths.map((sub: any, sIdx: number) => (
                    <div key={sIdx} className="flex flex-col">
                      <div className="flex items-start gap-3">
                        <div className={`w-16 h-7 rounded-full flex items-center justify-center font-bold text-xs flex-shrink-0 ${
                          sub.traffic_type === 1 ? "bg-green-100 text-green-700" :
                          sub.traffic_type === 2 ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600"
                        }`}>
                          {sub.traffic_type === 1 ? "지하철" : sub.traffic_type === 2 ? "버스" : "도보"}
                        </div>
                        <div className="flex-1 pt-0.5">
                          <div className="font-semibold text-gray-800 text-sm">
                            {sub.traffic_type === 3 ? "도보 이동" : `${sub.start_name} ➡️ ${sub.end_name}`}
                          </div>
                          <div className="text-xs text-gray-500 mt-0.5 flex items-center flex-wrap gap-1.5">
                            {sub.traffic_type !== 3 && sub.lane_name && (
                              <span className="font-medium text-gray-700">[{sub.lane_name}]</span>
                            )}
                            <span>{sub.section_time_min}분 소요</span>
                            {sub.station_count > 0 && <span>({sub.station_count}개 정거장)</span>}
                            {sub.traffic_type === 2 && sub.bus_congestion && (
                              <CongestionBadge level={sub.bus_congestion.level} />
                            )}
                          </div>
                        </div>
                      </div>
                      {sIdx < currentRoute.sub_paths.length - 1 && (
                        <div className="w-0.5 h-4 bg-gray-200 ml-8 my-1" />
                      )}
                    </div>
                  ))
                ) : (
                  <div className="space-y-3">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center font-semibold text-sm">출</div>
                      <div>
                        <div className="font-semibold text-gray-800">{currentRoute.first_start_station || departure}</div>
                        <div className="text-xs text-gray-500">출발역</div>
                      </div>
                    </div>
                    <div className="w-0.5 h-6 bg-gray-300 ml-4" />
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-red-100 text-red-600 flex items-center justify-center font-semibold text-sm">도</div>
                      <div>
                        <div className="font-semibold text-gray-800">{currentRoute.last_end_station || arrival}역</div>
                        <div className="text-xs text-gray-500">도착역</div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {isRushHour && selectedIdx < 3 && data?.rush_hour_result ? (
              <div className="bg-orange-50 border border-orange-200 rounded-xl p-4">
                <div className="flex items-start gap-2">
                  <span className="text-xl">🤖</span>
                  <div className="flex-1">
                    <h4 className="font-semibold text-orange-900 mb-2">AI 러시아워 분석</h4>
                    <p className="text-sm text-orange-800 mb-2 leading-relaxed">
                      {data.rush_hour_result.rush_hour_tip}
                    </p>
                    {data.rush_hour_result.alternative && (
                      <div className="bg-orange-100 rounded-lg px-3 py-2 text-sm text-orange-700">
                        💡 {data.rush_hour_result.alternative}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ) : isRushHour && selectedIdx >= 3 ? (
              <div className="bg-gray-50 border border-gray-200 rounded-xl p-4">
                <div className="flex items-start gap-2">
                  <TrendingDown className="w-5 h-5 text-gray-500 mt-0.5" />
                  <div>
                    <h4 className="font-semibold text-gray-700 mb-1">일반 경로</h4>
                    <p className="text-sm text-gray-600">AI 추천 없이 ODsay 기본 경로입니다.</p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
                <div className="flex items-start gap-2">
                  <TrendingDown className="w-5 h-5 text-blue-600 mt-0.5" />
                  <div>
                    <h4 className="font-semibold text-blue-900 mb-1">혼잡도 정보</h4>
                    <p className="text-sm text-blue-800">현재는 러시아워 시간대가 아닙니다.</p>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="bg-white border-t border-gray-200 p-4">
            <button
              onClick={() => navigate("/navigation", { state: { route: currentRoute, departure, arrival } })}
              className="w-full bg-blue-600 text-white py-3 rounded-xl font-semibold hover:bg-blue-700 transition-colors"
            >
              출발하기
            </button>
          </div>
        </>
      )}

      {!loading && !error && (routes.length === 0 || !currentRoute) && (
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="text-center space-y-3">
            <p className="text-gray-500">검색된 경로가 없습니다.</p>
            <button onClick={() => navigate(-1)} className="px-6 py-2 bg-blue-600 text-white rounded-xl font-semibold">돌아가기</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── 내비게이션 화면 ─────────────────────────────────────────────
function NavigationScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const { route, departure, arrival } = (location.state as {
    route: any; departure: string; arrival: string;
  }) || {};

  const mapRef = useRef<HTMLDivElement>(null);
  const kakaoMapRef = useRef<any>(null);
  const [loading, setLoading] = useState(true);

  const subPaths: any[] = route?.sub_paths ?? [];

  // sub_paths에서 표시할 주요 지점 이름 추출 (도보 제외, 중복 제거)
  const getKeyStations = (): { name: string; label: string; type: number }[] => {
    const points: { name: string; label: string; type: number }[] = [];
    const seen = new Set<string>();

    const add = (name: string, label: string, type: number) => {
      if (name && !seen.has(name)) {
        seen.add(name);
        points.push({ name, label, type });
      }
    };

    // 출발지
    add(departure, "출발", 0);

    for (let i = 0; i < subPaths.length; i++) {
      const sub = subPaths[i];

      if (sub.traffic_type === 3) {
        // 도보 구간이더라도, 다음 구간이 버스/지하철이라면
        // 이 도보 구간의 end_name이 탑승 정류장이므로 핀을 찍어야 함
        const next = subPaths[i + 1];
        if (next && next.traffic_type !== 3 && sub.end_name) {
          add(sub.end_name, sub.end_name, next.traffic_type);
        }
        continue;
      }

      if (sub.start_name) add(sub.start_name, sub.start_name, sub.traffic_type);
      if (sub.end_name)   add(sub.end_name,   sub.end_name,   sub.traffic_type);
    }

    // 도착지
    add(arrival, "도착", 0);

    return points;
  };

  const keyStations = getKeyStations();

  useEffect(() => {
    const initMap = () => {
      if (!mapRef.current || !window.kakao?.maps) return;
      const kakao = window.kakao;

      // 서울 시청 기본 중심
      const map = new kakao.maps.Map(mapRef.current, {
        center: new kakao.maps.LatLng(37.5665, 126.9780),
        level: 7,
      });
      kakaoMapRef.current = map;

      const geocoder = new kakao.maps.services.Geocoder();
      const bounds = new kakao.maps.LatLngBounds();
      const positions: any[] = [];
      let resolved = 0;

      if (keyStations.length === 0) { setLoading(false); return; }

      keyStations.forEach((station, idx) => {
        // 출발지(idx===0), 도착지(idx===마지막)는 일반 주소로 먼저 검색
        const isDeparture = idx === 0;
        const isArrival   = idx === keyStations.length - 1;

        const onResolved = (lat: number, lng: number) => {
          positions[idx] = { lat, lng, label: station.label, type: station.type };
          bounds.extend(new kakao.maps.LatLng(lat, lng));
          resolved++;
          if (resolved === keyStations.length) drawMarkers();
        };
        const onFailed = () => {
          resolved++;
          if (resolved === keyStations.length) drawMarkers();
        };

        if (isDeparture || isArrival) {
          // ① 일반 주소 검색 (지번/도로명)
          geocoder.addressSearch(station.name, (result: any[], status: string) => {
            if (status === kakao.maps.services.Status.OK && result.length > 0) {
              onResolved(parseFloat(result[0].y), parseFloat(result[0].x));
            } else {
              // ② 키워드 검색으로 폴백 (예: "성북구 동선동3가" 같은 동 단위)
              const ps = new kakao.maps.services.Places();
              ps.keywordSearch(station.name, (res: any[], st: string) => {
                if (st === kakao.maps.services.Status.OK && res.length > 0) {
                  onResolved(parseFloat(res[0].y), parseFloat(res[0].x));
                } else {
                  onFailed();
                }
              });
            }
          });
        } else if (station.type === 2) {
          // 버스 정류장: "역" 없이 키워드 검색
          const ps = new kakao.maps.services.Places();
          ps.keywordSearch(station.name, (res: any[], st: string) => {
            if (st === kakao.maps.services.Status.OK && res.length > 0) {
              onResolved(parseFloat(res[0].y), parseFloat(res[0].x));
            } else {
              onFailed();
            }
          });
        } else {
          // 지하철역: 기존 방식 유지 (주소검색 → 키워드검색 + "역")
          geocoder.addressSearch(station.name + " 역", (result: any[], status: string) => {
            if (status === kakao.maps.services.Status.OK && result.length > 0) {
              onResolved(parseFloat(result[0].y), parseFloat(result[0].x));
            } else {
              const ps = new kakao.maps.services.Places();
              ps.keywordSearch(station.name + "역", (res: any[], st: string) => {
                if (st === kakao.maps.services.Status.OK && res.length > 0) {
                  onResolved(parseFloat(res[0].y), parseFloat(res[0].x));
                } else {
                  onFailed();
                }
              });
            }
          });
        }
      });

      const drawMarkers = () => {
        const validPos = positions.filter(Boolean);
        const lastIdx = validPos.length - 1;

        validPos.forEach((pos, i) => {
          const latlng = new kakao.maps.LatLng(pos.lat, pos.lng);
          const isDep = i === 0;
          const isArr = i === lastIdx && lastIdx > 0;

          // 출발·도착은 SVG 커스텀 오버레이로 표시, 역은 기본 마커
          if (isDep || isArr) {
            const color = isDep ? "#2563eb" : "#dc2626";
            const icon  = isDep
              ? `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="white"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5S10.62 6.5 12 6.5s2.5 1.12 2.5 2.5S13.38 11.5 12 11.5z"/></svg>`
              : `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="white"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5S10.62 6.5 12 6.5s2.5 1.12 2.5 2.5S13.38 11.5 12 11.5z"/></svg>`;

            const pinOverlay = new kakao.maps.CustomOverlay({
              position: latlng,
              content: `<div style="display:flex;flex-direction:column;align-items:center;gap:2px">
                <div style="background:${color};border-radius:20px;padding:4px 10px;font-size:11px;font-weight:700;color:#fff;white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,0.25)">${pos.label}</div>
                <div style="width:32px;height:32px;background:${color};border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,0.3)">${icon}</div>
                <div style="width:2px;height:8px;background:${color}"></div>
              </div>`,
              yAnchor: 1.0,
            });
            pinOverlay.setMap(map);
          } else {
            // 역: 기본 마커 + 말풍선 오버레이
            new kakao.maps.Marker({ position: latlng, map, title: pos.label });

            const labelColor = pos.type === 1 ? "#16a34a" : pos.type === 2 ? "#2563eb" : "#6b7280";
            const overlay = new kakao.maps.CustomOverlay({
              position: latlng,
              content: `<div style="background:#fff;color:${labelColor};border:1.5px solid ${labelColor};padding:3px 8px;border-radius:12px;font-size:11px;font-weight:700;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,0.18)">${pos.label}</div>`,
              yAnchor: 2.8,
            });
            overlay.setMap(map);
          }
        });

        // 전체 마커가 보이도록 bounds 설정
        if (validPos.length >= 2) {
          map.setBounds(bounds);
        } else if (validPos.length === 1) {
          map.setCenter(new kakao.maps.LatLng(validPos[0].lat, validPos[0].lng));
          map.setLevel(5);
        }

        setLoading(false);
      };
    };

    // services 라이브러리 포함해서 로드
    if (window.kakao?.maps?.services) {
      initMap();
    } else if (window.kakao?.maps) {
      window.kakao.maps.load(initMap);
    } else {
      const script = document.createElement("script");
      script.src = `//dapi.kakao.com/v2/maps/sdk.js?appkey=d9097bf569833a18d058b7b80eb76aaa&autoload=false&libraries=services`;
      script.onload = () => window.kakao.maps.load(initMap);
      document.head.appendChild(script);
    }
  }, []);

  return (
    <div className="size-full flex flex-col bg-gray-900">
      {/* 상단 헤더 */}
      <div className="bg-white border-b border-gray-200 px-4 py-3 flex items-center gap-3 z-10">
        <button onClick={() => navigate(-1)} className="text-blue-600">
          <X className="w-6 h-6" />
        </button>
        <div className="flex-1 min-w-0">
          <div className="text-xs text-gray-500 truncate">{departure}</div>
          <div className="text-sm font-bold text-gray-800 truncate">→ {arrival}</div>
        </div>
      </div>

      {/* 카카오 지도 */}
      <div className="relative flex-1">
        <div ref={mapRef} className="w-full h-full" />
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-100">
            <div className="text-center space-y-2">
              <div className="w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full animate-spin mx-auto" />
              <p className="text-sm text-gray-500">지도 불러오는 중...</p>
            </div>
          </div>
        )}
      </div>

      {/* 하단 경로 요약 */}
      <div className="bg-white border-t border-gray-200 px-4 pt-3 pb-4 space-y-2 max-h-48 overflow-y-auto">
        {subPaths.length > 0 ? (
          subPaths.map((sub: any, i: number) => (
            <div key={i} className="flex items-center gap-3">
              <div className={`w-14 h-6 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${
                sub.traffic_type === 1 ? "bg-green-100 text-green-700" :
                sub.traffic_type === 2 ? "bg-blue-100 text-blue-700" :
                "bg-gray-100 text-gray-600"
              }`}>
                {sub.traffic_type === 1 ? "지하철" : sub.traffic_type === 2 ? "버스" : "도보"}
              </div>
              <span className="text-sm text-gray-700 truncate">
                {sub.traffic_type === 3
                  ? `도보 ${sub.section_time_min}분`
                  : `${sub.start_name} → ${sub.end_name}${sub.lane_name ? ` [${sub.lane_name}]` : ""}`}
              </span>
            </div>
          ))
        ) : (
          <p className="text-sm text-gray-500 text-center py-2">경로 정보 없음</p>
        )}
      </div>
    </div>
  );
}
// ────────────────────────────────────────────────────────────────

function SignupScreen() {
  const navigate = useNavigate();
  const [formData, setFormData] = useState({ username: "", password: "", confirmPassword: "", phone: "", email: "" });
  const [usernameAvailable, setUsernameAvailable] = useState<boolean | null>(null);
  const [isCheckingUsername, setIsCheckingUsername] = useState(false);

  const handleCheckUsername = () => {
    if (!formData.username) return;
    setIsCheckingUsername(true);
    const users = JSON.parse(localStorage.getItem("users") || "[]");
    const exists = users.some((u: any) => u.username === formData.username);
    setUsernameAvailable(!exists);
    setIsCheckingUsername(false);
  };

  const handleSubmit = () => {
    if (!formData.username || !formData.password || !formData.phone || !formData.email) { alert("모든 항목을 입력해주세요."); return; }
    if (formData.password !== formData.confirmPassword) { alert("비밀번호가 일치하지 않습니다."); return; }
    if (!usernameAvailable) { alert("아이디 중복확인을 해주세요."); return; }
    const users = JSON.parse(localStorage.getItem("users") || "[]");
    const newUser = { username: formData.username, password: formData.password, email: formData.email, phone: formData.phone };
    users.push(newUser);
    localStorage.setItem("users", JSON.stringify(users));
    // 자동 로그인 후 프로필 선택으로 이동
    sessionStorage.setItem("isLoggedIn", "true");
    sessionStorage.setItem("loggedInUser", JSON.stringify(newUser));
    navigate("/profile-select");
  };

  return (
    <div className="size-full bg-gray-50 flex flex-col">
      <div className="bg-white border-b border-gray-200 p-4 flex items-center gap-3">
        <button onClick={() => navigate("/")} className="text-blue-600"><X className="w-6 h-6" /></button>
        <h1 className="text-xl font-bold text-gray-800">회원가입</h1>
      </div>
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">아이디</label>
          <div className="flex gap-2">
            <input type="text" value={formData.username} onChange={(e) => { setFormData({ ...formData, username: e.target.value }); setUsernameAvailable(null); }} placeholder="아이디 입력" className="flex-1 px-4 py-3 bg-white border border-gray-300 rounded-xl outline-none focus:border-blue-500" />
            <button onClick={handleCheckUsername} disabled={!formData.username || isCheckingUsername} className="px-4 py-3 bg-blue-600 text-white rounded-xl font-semibold disabled:bg-gray-300 hover:bg-blue-700 transition-colors whitespace-nowrap">
              {isCheckingUsername ? "확인중..." : "중복확인"}
            </button>
          </div>
          {usernameAvailable !== null && (
            <div className={`mt-2 text-sm flex items-center gap-1 ${usernameAvailable ? "text-green-600" : "text-red-600"}`}>
              {usernameAvailable ? <Check className="w-4 h-4" /> : <X className="w-4 h-4" />}
              <span>{usernameAvailable ? "사용 가능한 아이디입니다" : "이미 사용중인 아이디입니다"}</span>
            </div>
          )}
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">비밀번호</label>
          <input type="password" value={formData.password} onChange={(e) => setFormData({ ...formData, password: e.target.value })} placeholder="비밀번호 입력" className="w-full px-4 py-3 bg-white border border-gray-300 rounded-xl outline-none focus:border-blue-500" />
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">비밀번호 확인</label>
          <input type="password" value={formData.confirmPassword} onChange={(e) => setFormData({ ...formData, confirmPassword: e.target.value })} placeholder="비밀번호 재입력" className="w-full px-4 py-3 bg-white border border-gray-300 rounded-xl outline-none focus:border-blue-500" />
          {formData.confirmPassword && formData.password !== formData.confirmPassword && (
            <div className="mt-2 text-sm text-red-600 flex items-center gap-1"><X className="w-4 h-4" /><span>비밀번호가 일치하지 않습니다</span></div>
          )}
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">전화번호</label>
          <input type="tel" value={formData.phone} onChange={(e) => setFormData({ ...formData, phone: e.target.value })} placeholder="010-1234-5678" className="w-full px-4 py-3 bg-white border border-gray-300 rounded-xl outline-none focus:border-blue-500" />
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">이메일</label>
          <input type="email" value={formData.email} onChange={(e) => setFormData({ ...formData, email: e.target.value })} placeholder="example@email.com" className="w-full px-4 py-3 bg-white border border-gray-300 rounded-xl outline-none focus:border-blue-500" />
        </div>
      </div>
      <div className="bg-white border-t border-gray-200 p-4">
        <button onClick={handleSubmit} className="w-full bg-blue-600 text-white py-3 rounded-xl font-semibold hover:bg-blue-700 transition-colors">가입하기</button>
      </div>
    </div>
  );
}

function LoginScreen() {
  const navigate = useNavigate();
  const [formData, setFormData] = useState({ username: "", password: "" });
  const [error, setError] = useState("");

  const handleLogin = () => {
    if (!formData.username || !formData.password) { setError("아이디와 비밀번호를 입력해주세요."); return; }
    const users = JSON.parse(localStorage.getItem("users") || "[]");
    const user = users.find((u: any) => u.username === formData.username && u.password === formData.password);
    if (!user) { setError("아이디 또는 비밀번호가 올바르지 않습니다."); return; }
    sessionStorage.setItem("isLoggedIn", "true");
    sessionStorage.setItem("loggedInUser", JSON.stringify(user));
    navigate("/account");
  };

  return (
    <div className="size-full bg-gray-50 flex flex-col">
      <div className="bg-white border-b border-gray-200 p-4 flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="text-blue-600"><X className="w-6 h-6" /></button>
        <h1 className="text-xl font-bold text-gray-800">로그인</h1>
      </div>
      <div className="flex-1 flex flex-col justify-center p-6 space-y-5">
        <div className="flex flex-col items-center mb-4">
          <div className="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center mb-3">
            <User className="w-10 h-10 text-blue-600" />
          </div>
          <h2 className="text-2xl font-bold text-gray-800">여유로</h2>
          <p className="text-sm text-gray-500 mt-1">계정에 로그인하세요</p>
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">아이디</label>
          <input type="text" value={formData.username} onChange={(e) => { setFormData({ ...formData, username: e.target.value }); setError(""); }} placeholder="아이디 입력" className="w-full px-4 py-3 bg-white border border-gray-300 rounded-xl outline-none focus:border-blue-500" />
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">비밀번호</label>
          <input type="password" value={formData.password} onChange={(e) => { setFormData({ ...formData, password: e.target.value }); setError(""); }} placeholder="비밀번호 입력" className="w-full px-4 py-3 bg-white border border-gray-300 rounded-xl outline-none focus:border-blue-500" onKeyDown={(e) => { if (e.key === "Enter") handleLogin(); }} />
        </div>
        {error && (
          <div className="flex items-center gap-2 text-red-600 text-sm bg-red-50 p-3 rounded-xl">
            <X className="w-4 h-4 flex-shrink-0" /><span>{error}</span>
          </div>
        )}
        <button onClick={handleLogin} className="w-full bg-blue-600 text-white py-3 rounded-xl font-semibold hover:bg-blue-700 transition-colors">로그인</button>
        <button onClick={() => navigate("/signup")} className="w-full bg-gray-100 text-gray-700 py-3 rounded-xl font-semibold hover:bg-gray-200 transition-colors">회원가입</button>
      </div>
    </div>
  );
}

function MapScreen() {
  return (
    <div className="size-full flex flex-col bg-gray-50">
      <div className="bg-white border-b border-gray-200 p-4">
        <h1 className="text-xl font-bold text-gray-800">지도</h1>
      </div>
      <div className="flex-1 bg-gray-100 flex items-center justify-center">
        <div className="text-center">
          <Map className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <p className="text-gray-500">지도 화면</p>
        </div>
      </div>
      <BottomNavigation />
    </div>
  );
}

function AccountScreen() {
  const navigate = useNavigate();
  const [isLoggedIn, setIsLoggedIn] = useState(() => sessionStorage.getItem("isLoggedIn") === "true");
  const [currentUser, setCurrentUser] = useState<{ username: string; email: string; avatarId?: string } | null>(() => {
    try { return JSON.parse(sessionStorage.getItem("loggedInUser") || "null"); } catch { return null; }
  });

  // 탈퇴 모달 단계: null | "reason" | "confirm"
  const [withdrawStep, setWithdrawStep] = useState<null | "reason" | "confirm">(null);
  const WITHDRAW_REASONS = [
    "더 이상 사용하지 않아요",
    "앱이 불편하거나 오류가 많아요",
    "개인정보가 걱정돼요",
    "다른 앱으로 이동했어요",
  ];
  const [selectedReason, setSelectedReason] = useState<string | null>(null);
  const [customReason, setCustomReason] = useState("");

  const handleLogout = () => {
    sessionStorage.removeItem("isLoggedIn");
    sessionStorage.removeItem("loggedInUser");
    setIsLoggedIn(false);
    setCurrentUser(null);
  };

  const handleWithdraw = () => {
    if (!currentUser) return;
    // 계정 및 개인정보 전부 삭제
    const users: any[] = JSON.parse(localStorage.getItem("users") || "[]");
    const filtered = users.filter((u: any) => u.username !== currentUser.username);
    localStorage.setItem("users", JSON.stringify(filtered));
    sessionStorage.removeItem("isLoggedIn");
    sessionStorage.removeItem("loggedInUser");
    setWithdrawStep(null);
    setIsLoggedIn(false);
    setCurrentUser(null);
    navigate("/");
  };

  const avatar = currentUser?.avatarId ? getAvatar(currentUser.avatarId) : null;

  return (
    <div className="size-full flex flex-col bg-gray-50">
      <div className="bg-white border-b border-gray-200 p-4">
        <h1 className="text-xl font-bold text-gray-800">내 계정</h1>
      </div>
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        <div className="bg-white rounded-xl p-6 shadow-md text-center">
          <div className="w-20 h-20 rounded-full overflow-hidden flex items-center justify-center mx-auto mb-4 bg-blue-100">
            {avatar ? (
              <img src={avatar.img} alt={avatar.label} className="w-full h-full object-cover" />
            ) : (
              <User className="w-10 h-10 text-blue-600" />
            )}
          </div>
          {isLoggedIn && currentUser ? (
            <>
              <h2 className="text-xl font-bold text-gray-800">{currentUser.username}</h2>
              <p className="text-sm text-gray-600 mt-1">{currentUser.email}</p>
            </>
          ) : (
            <h2 className="text-xl font-bold text-gray-800">Guest</h2>
          )}
        </div>
        <div className="bg-white rounded-xl shadow-md overflow-hidden">
          {isLoggedIn ? (
            <>
              <button onClick={() => navigate("/profile-edit")} className="w-full p-4 text-left hover:bg-gray-50 border-b border-gray-100"><span className="text-gray-800 font-medium">프로필 수정</span></button>
              <button className="w-full p-4 text-left hover:bg-gray-50 border-b border-gray-100" onClick={() => navigate("/notification-settings")}><span className="text-gray-800 font-medium">알림 설정</span></button>
              <button onClick={handleLogout} className="w-full p-4 text-left hover:bg-gray-50 border-b border-gray-100"><span className="text-gray-800 font-medium">로그아웃</span></button>
              <button onClick={() => { setSelectedReason(null); setCustomReason(""); setWithdrawStep("reason"); }} className="w-full p-4 text-left hover:bg-gray-50"><span className="text-red-500 font-medium">계정 탈퇴</span></button>
            </>
          ) : (
            <>
              <button onClick={() => navigate("/login")} className="w-full p-4 text-left hover:bg-gray-50 border-b border-gray-100"><span className="text-blue-600 font-medium">로그인</span></button>
              <button onClick={() => navigate("/signup")} className="w-full p-4 text-left hover:bg-gray-50"><span className="text-gray-800 font-medium">회원가입</span></button>
            </>
          )}
        </div>
      </div>
      <BottomNavigation />

      {/* 탈퇴 이유 선택 모달 */}
      {withdrawStep === "reason" && (
        <div className="fixed inset-0 bg-black/50 flex items-end justify-center z-50">
          <div className="bg-white rounded-t-3xl w-full p-6 space-y-4 max-h-[85vh] overflow-y-auto">
            <h2 className="text-lg font-bold text-gray-800">탈퇴하는 이유를 알려주세요</h2>
            <p className="text-sm text-gray-500">더 나은 서비스를 위해 소중한 의견을 반영할게요.</p>
            <div className="space-y-2">
              {WITHDRAW_REASONS.map((reason) => (
                <button
                  key={reason}
                  onClick={() => setSelectedReason(reason === selectedReason ? null : reason)}
                  className={`w-full flex items-center gap-3 p-3 rounded-xl border text-left transition-colors ${
                    selectedReason === reason ? "border-red-400 bg-red-50" : "border-gray-200 bg-gray-50 hover:border-gray-300"
                  }`}
                >
                  <div className={`w-5 h-5 rounded-full border-2 flex-shrink-0 flex items-center justify-center ${
                    selectedReason === reason ? "border-red-500 bg-red-500" : "border-gray-300"
                  }`}>
                    {selectedReason === reason && <div className="w-2 h-2 bg-white rounded-full" />}
                  </div>
                  <span className="text-sm text-gray-700">{reason}</span>
                </button>
              ))}
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">기타</label>
              <textarea
                value={customReason}
                onChange={(e) => { setCustomReason(e.target.value); if (e.target.value) setSelectedReason(null); }}
                placeholder="직접 이유를 입력해주세요 (선택)"
                className="w-full px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl outline-none focus:border-red-400 resize-none text-sm text-gray-700 placeholder-gray-400"
                rows={3}
              />
            </div>
            <div className="flex gap-3 pt-1">
              <button
                onClick={() => setWithdrawStep(null)}
                className="flex-1 py-3 rounded-xl bg-gray-100 text-gray-700 font-semibold hover:bg-gray-200 transition-colors"
              >
                취소
              </button>
              <button
                onClick={() => setWithdrawStep("confirm")}
                disabled={!selectedReason && !customReason.trim()}
                className="flex-1 py-3 rounded-xl bg-red-500 text-white font-semibold hover:bg-red-600 transition-colors disabled:bg-gray-300 disabled:cursor-not-allowed"
              >
                탈퇴
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 최종 확인 모달 */}
      {withdrawStep === "confirm" && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-6">
          <div className="bg-white rounded-2xl w-full max-w-sm p-6 space-y-5">
            <div className="text-center space-y-2">
              <p className="text-3xl">⚠️</p>
              <h3 className="text-lg font-bold text-gray-800">정말 탈퇴하시겠습니까?</h3>
              <p className="text-sm text-gray-500">탈퇴하시면 계정과 모든 개인정보가 즉시 삭제되며 복구할 수 없습니다.</p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setWithdrawStep("reason")}
                className="flex-1 py-3 rounded-xl bg-gray-100 text-gray-700 font-semibold hover:bg-gray-200 transition-colors"
              >
                아니오
              </button>
              <button
                onClick={handleWithdraw}
                className="flex-1 py-3 rounded-xl bg-red-500 text-white font-semibold hover:bg-red-600 transition-colors"
              >
                예
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
} 

const TIP_VISIBLE_KEY = "tipBannerVisible";

function getTipKey() {
  try {
    const user = JSON.parse(sessionStorage.getItem("loggedInUser") || "null");
    return user?.username ? `${TIP_VISIBLE_KEY}_${user.username}` : null;
  } catch { return null; }
}

function NotificationSettingsScreen() {
  const navigate = useNavigate();
  const [tipEnabled, setTipEnabled] = useState<boolean>(() => {
    const key = getTipKey();
    if (!key) return true;
    const stored = localStorage.getItem(key);
    return stored === null ? true : stored === "true";
  });

  const handleToggle = () => {
    const key = getTipKey();
    if (!key) return;
    const next = !tipEnabled;
    setTipEnabled(next);
    localStorage.setItem(key, String(next));
  };

  return (
    <div className="size-full bg-gray-50 flex flex-col">
      <div className="bg-white border-b border-gray-200 p-4 flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="text-blue-600"><X className="w-6 h-6" /></button>
        <h1 className="text-xl font-bold text-gray-800">알림 설정</h1>
      </div>
      <div className="flex-1 p-6 space-y-4">
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
          <div className="p-4 flex items-center justify-between">
            <div>
              <p className="font-semibold text-gray-800">홈 화면 팁 배너</p>
              <p className="text-sm text-gray-500 mt-0.5">홈 화면 상단의 팁/공지 띠배너 표시</p>
            </div>
            <button
              onClick={handleToggle}
              className={`relative w-12 h-6 rounded-full transition-colors duration-200 focus:outline-none ${tipEnabled ? "bg-blue-600" : "bg-gray-300"}`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200 ${tipEnabled ? "translate-x-6" : "translate-x-0"}`}
              />
            </button>
          </div>
        </div>
        <p className="text-xs text-gray-400 px-1">설정은 이 기기에 저장됩니다.</p>
      </div>
    </div>
  );
}

function ProfileEditScreen() {
  const navigate = useNavigate();
  const [currentUser, setCurrentUser] = useState<{ username: string; email: string; phone?: string; password: string; avatarId?: string } | null>(() => {
    try { return JSON.parse(sessionStorage.getItem("loggedInUser") || "null"); } catch { return null; }
  });
  const [formData, setFormData] = useState({
    email: currentUser?.email || "",
    phone: currentUser?.phone || "",
    newPassword: "",
    confirmPassword: "",
  });
  const [selectedAvatar, setSelectedAvatar] = useState<string>(currentUser?.avatarId || "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [successMsg, setSuccessMsg] = useState("");

  const validate = () => {
    const errs: Record<string, string> = {};
    if (!formData.email) errs.email = "이메일을 입력해주세요.";
    if (!formData.phone) errs.phone = "전화번호를 입력해주세요.";
    if (formData.newPassword && formData.newPassword !== formData.confirmPassword)
      errs.confirmPassword = "비밀번호가 일치하지 않습니다.";
    return errs;
  };

  const handleSave = () => {
    const errs = validate();
    if (Object.keys(errs).length > 0) { setErrors(errs); return; }
    const users: any[] = JSON.parse(localStorage.getItem("users") || "[]");
    const updatedUser = {
      ...currentUser!,
      email: formData.email,
      phone: formData.phone,
      avatarId: selectedAvatar || currentUser?.avatarId,
      ...(formData.newPassword ? { password: formData.newPassword } : {}),
    };
    const idx = users.findIndex((u) => u.username === currentUser?.username);
    if (idx !== -1) users[idx] = updatedUser;
    localStorage.setItem("users", JSON.stringify(users));
    sessionStorage.setItem("loggedInUser", JSON.stringify(updatedUser));
    setCurrentUser(updatedUser);
    setErrors({});
    setSuccessMsg("프로필이 저장되었습니다.");
    setFormData(prev => ({ ...prev, newPassword: "", confirmPassword: "" }));
    setTimeout(() => { setSuccessMsg(""); navigate("/account"); }, 1200);
  };

  if (!currentUser) {
    return (
      <div className="size-full flex items-center justify-center bg-gray-50">
        <p className="text-gray-500">로그인이 필요합니다.</p>
      </div>
    );
  }

  const currentAvatar = getAvatar(selectedAvatar || currentUser.avatarId);

  return (
    <div className="size-full bg-gray-50 flex flex-col">
      <div className="bg-white border-b border-gray-200 p-4 flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="text-blue-600"><X className="w-6 h-6" /></button>
        <h1 className="text-xl font-bold text-gray-800">프로필 수정</h1>
      </div>
      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* 현재 아바타 표시 */}
        <div className="flex flex-col items-center mb-2">
          <div className="w-20 h-20 rounded-full overflow-hidden mb-2 bg-blue-100 flex items-center justify-center">
            {currentAvatar ? (
              <img src={currentAvatar.img} alt={currentAvatar.label} className="w-full h-full object-cover" />
            ) : (
              <User className="w-10 h-10 text-blue-600" />
            )}
          </div>
          <p className="text-lg font-bold text-gray-800">{currentUser.username}</p>
          <p className="text-xs text-gray-400">아이디는 변경할 수 없습니다</p>
        </div>

        {/* 아바타 선택 */}
        <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100">
          <p className="text-sm font-semibold text-gray-700 mb-3">프로필 사진 변경</p>
          <div className="grid grid-cols-4 gap-3">
            {AVATAR_OPTIONS.map(avatar => (
              <button
                key={avatar.id}
                onClick={() => setSelectedAvatar(avatar.id)}
                className={`flex flex-col items-center gap-1.5 p-2 rounded-xl border-2 transition-all ${
                  (selectedAvatar || currentUser.avatarId) === avatar.id
                    ? "border-blue-500 bg-blue-50"
                    : "border-gray-100 bg-gray-50 hover:border-gray-300"
                }`}
              >
                <div className="w-12 h-12 rounded-full overflow-hidden">
                  <img src={avatar.img} alt={avatar.label} className="w-full h-full object-cover" />
                </div>
                <span className="text-xs text-gray-600 font-medium">{avatar.label}</span>
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">이메일</label>
          <input type="email" value={formData.email} onChange={(e) => { setFormData({ ...formData, email: e.target.value }); setErrors({ ...errors, email: "" }); }} placeholder="example@email.com" className={`w-full px-4 py-3 bg-white border rounded-xl outline-none focus:border-blue-500 ${errors.email ? "border-red-400" : "border-gray-300"}`} />
          {errors.email && <p className="mt-1 text-xs text-red-500">{errors.email}</p>}
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">전화번호</label>
          <input type="tel" value={formData.phone} onChange={(e) => { setFormData({ ...formData, phone: e.target.value }); setErrors({ ...errors, phone: "" }); }} placeholder="010-1234-5678" className={`w-full px-4 py-3 bg-white border rounded-xl outline-none focus:border-blue-500 ${errors.phone ? "border-red-400" : "border-gray-300"}`} />
          {errors.phone && <p className="mt-1 text-xs text-red-500">{errors.phone}</p>}
        </div>
        <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 space-y-4">
          <p className="text-sm font-semibold text-gray-700">비밀번호 변경 <span className="text-gray-400 font-normal">(선택)</span></p>
          <div>
            <label className="block text-xs text-gray-500 mb-1">새 비밀번호</label>
            <input type="password" value={formData.newPassword} onChange={(e) => { setFormData({ ...formData, newPassword: e.target.value }); setErrors({ ...errors, confirmPassword: "" }); }} placeholder="새 비밀번호 입력" className="w-full px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">비밀번호 확인</label>
            <input type="password" value={formData.confirmPassword} onChange={(e) => { setFormData({ ...formData, confirmPassword: e.target.value }); setErrors({ ...errors, confirmPassword: "" }); }} placeholder="새 비밀번호 재입력" className={`w-full px-4 py-3 bg-gray-50 border rounded-xl outline-none focus:border-blue-500 ${errors.confirmPassword ? "border-red-400" : "border-gray-200"}`} />
            {errors.confirmPassword && <p className="mt-1 text-xs text-red-500">{errors.confirmPassword}</p>}
            {formData.newPassword && formData.confirmPassword && formData.newPassword === formData.confirmPassword && (
              <p className="mt-1 text-xs text-green-600 flex items-center gap-1"><Check className="w-3 h-3" />비밀번호가 일치합니다</p>
            )}
          </div>
        </div>
        {successMsg && (
          <div className="flex items-center gap-2 text-green-700 bg-green-50 border border-green-200 rounded-xl px-4 py-3 text-sm">
            <Check className="w-4 h-4 flex-shrink-0" /><span>{successMsg}</span>
          </div>
        )}
      </div>
      <div className="bg-white border-t border-gray-200 p-4">
        <button onClick={handleSave} className="w-full bg-blue-600 text-white py-3 rounded-xl font-semibold hover:bg-blue-700 transition-colors">저장하기</button>
      </div>
    </div>
  );
}

function AppContent() {
  const [showSplash, setShowSplash] = useState(true);
  const [showSignupPrompt, setShowSignupPrompt] = useState(false);
  const [isFirstVisit, setIsFirstVisit] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    const hasVisited = localStorage.getItem("hasVisited");
    if (hasVisited) setIsFirstVisit(false);
  }, []);

  const handleSplashComplete = () => {
    setShowSplash(false);
    if (isFirstVisit) setShowSignupPrompt(true);
  };

  const handleSignupResponse = (wantsSignup: boolean) => {
    setShowSignupPrompt(false);
    localStorage.setItem("hasVisited", "true");
    setIsFirstVisit(false);
    if (wantsSignup) navigate("/signup");
  };

  if (showSplash) return <SplashScreen onComplete={handleSplashComplete} />;

  return (
    <>
      <Routes>
        <Route path="/" element={<MainScreen />} />
        <Route path="/routes" element={<RouteResultScreen />} />
        <Route path="/map" element={<MapScreen />} />
        <Route path="/account" element={<AccountScreen />} />
        <Route path="/signup" element={<SignupScreen />} />
        <Route path="/login" element={<LoginScreen />} />
        <Route path="/profile-edit" element={<ProfileEditScreen />} />
        <Route path="/profile-select" element={<ProfileSelectScreen />} />
        <Route path="/notification-settings" element={<NotificationSettingsScreen />} />
        <Route path="/navigation" element={<NavigationScreen />} />
      </Routes>

      {showSignupPrompt && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl p-6 max-w-sm w-full space-y-4">
            <h3 className="text-lg font-bold text-gray-800 text-center">환영합니다!</h3>
            <p className="text-center text-gray-700">처음 이용하신다면 회원가입을 진행하시겠어요?</p>
            <div className="flex gap-3">
              <button onClick={() => handleSignupResponse(false)} className="flex-1 bg-gray-200 text-gray-800 py-3 rounded-xl font-semibold hover:bg-gray-300 transition-colors">아니요</button>
              <button onClick={() => handleSignupResponse(true)} className="flex-1 bg-blue-600 text-white py-3 rounded-xl font-semibold hover:bg-blue-700 transition-colors">네</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default function App() {
  return (
    <Router>
      <div className="size-full">
        <AppContent />
      </div>
    </Router>
  );
}
  