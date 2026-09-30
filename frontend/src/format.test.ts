import { minutes, pct } from "./format";

it("formats durations for detection results", () => {
  expect(minutes(2.75)).toBe("2.8 min");
  expect(minutes(3)).toBe("3 min");
  expect(minutes(224)).toBe("3.7 h");
  expect(minutes(null)).toBe("–");
});

it("formats percentages", () => {
  expect(pct(0.9473)).toBe("95%");
  expect(pct(0.99963, 3)).toBe("99.963%");
  expect(pct(undefined)).toBe("–");
});
