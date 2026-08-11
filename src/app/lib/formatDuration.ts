// src/lib/formatDuration.ts

/**
 * 분(minute) 단위 숫자를 "1시간 30분" 같은 한국어 표기로 변환합니다.
 * - 90  -> "1시간 30분"
 * - 45  -> "45분"
 * - 120 -> "2시간"
 * - 0 이하 -> "0분"
 */
export function formatDuration(totalMinutes: number): string {
  if (!Number.isFinite(totalMinutes) || totalMinutes <= 0) return "0분";

  const hours = Math.floor(totalMinutes / 60);
  const minutes = Math.round(totalMinutes % 60);

  if (hours === 0) return `${minutes}분`;
  if (minutes === 0) return `${hours}시간`;
  return `${hours}시간 ${minutes}분`;
}

// 사용 예시
//   <span>{formatDuration(routeResult.durationMinutes)}</span>
// 기존에 `${durationMinutes}분` 으로 그대로 찍던 부분을
// formatDuration(durationMinutes) 호출로 바꾸면 됩니다.
