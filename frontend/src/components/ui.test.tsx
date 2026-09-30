import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { IncidentRow } from "../api";
import { IncidentStrip } from "./ui";

const row = (id: string, predicted: string, actual: string): IncidentRow => ({
  id, fired_at: "2026-08-02T12:00:00", ended_at: "2026-08-02T12:10:00", service: "orders-service", signal: "error_rate",
  peak_z: 12, predicted: predicted as IncidentRow["predicted"], confidence: 0.9, rules_prediction: "unknown", status: "resolved",
  actual: actual as IncidentRow["actual"], unknown_reason: null,
});

it("flags misclassified alerts but treats unknown for a held-out leak as correct", () => {
  const { container } = render(
    <MemoryRouter>
      <IncidentStrip
        incidents={[row("INC-1", "traffic_spike", "database_saturation"), row("INC-2", "unknown", "memory_leak"), row("INC-3", "traffic_spike", "traffic_spike")]}
        start="2026-07-31T00:00:00"
        end="2026-08-07T00:00:00"
      />
    </MemoryRouter>,
  );
  expect(container.querySelectorAll(".strip-mark.actual.miss")).toHaveLength(1);
  expect(container.querySelectorAll("a.strip-mark")).toHaveLength(3);
});
