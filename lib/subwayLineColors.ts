// src/lib/subwayLineColors.ts

export const subwayLineColors: { [key: string]: string } = {
  "1호선": "#0052A4", // Pantone 286C
  "2호선": "#00A84D", // Pantone 354C
  "3호선": "#EF7C1C", // Pantone 1655C
  "4호선": "#00A8E1", // Pantone 2995C
  "5호선": "#9932CC", // Pantone 2583C
  "6호선": "#CD5C2C", // Pantone 1675C
  "7호선": "#747F00", // Pantone 5757C
  "8호선": "#EA5455", // Pantone 213C
  "9호선": "#BDB092", // Pantone 872C
};

// 노선에 색상이 없을 때 (신설 노선 등) 대비용 기본색
const DEFAULT_LINE_COLOR = "#6B7280"; // gray-500

/**
 * 노선명으로 배경색을 가져옵니다. 못 찾으면 기본 회색을 반환합니다.
 */
export function getSubwayLineColor(lineName: string): string {
  return subwayLineColors[lineName] ?? DEFAULT_LINE_COLOR;
}

/**
 * 배경색의 밝기(YIQ)를 계산해 검정/흰색 중 더 잘 보이는 글자색을 반환합니다.
 * 9호선(#BDB092) 같은 밝은 색에는 자동으로 검정 글씨가 나옵니다.
 */
export function getReadableTextColor(hexColor: string): "#FFFFFF" | "#111111" {
  const hex = hexColor.replace("#", "");
  const r = parseInt(hex.substring(0, 2), 16);
  const g = parseInt(hex.substring(2, 4), 16);
  const b = parseInt(hex.substring(4, 6), 16);
  const yiq = (r * 299 + g * 587 + b * 114) / 1000;
  return yiq >= 150 ? "#111111" : "#FFFFFF";
}
