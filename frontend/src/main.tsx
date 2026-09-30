import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { Layout } from "./components/ui";
import { Assistant } from "./pages/Assistant";
import { Classifier } from "./pages/Classifier";
import { Detection } from "./pages/Detection";
import { Drift } from "./pages/Drift";
import { Gate } from "./pages/Gate";
import { Incident } from "./pages/Incident";
import { Overview } from "./pages/Overview";
import { Slos } from "./pages/Slos";
import "./styles.css";

const client = new QueryClient({ defaultOptions: { queries: { staleTime: 60_000, retry: 2, refetchOnWindowFocus: false } } });

function NotFound() {
  return (
    <div className="page">
      <h1>This page doesn't exist</h1>
      <p className="lede">
        <Link to="/">Back to the overview</Link>
      </p>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="incident/:id" element={<Incident />} />
            <Route path="detection" element={<Detection />} />
            <Route path="classifier" element={<Classifier />} />
            <Route path="slos" element={<Slos />} />
            <Route path="assistant" element={<Assistant />} />
            <Route path="drift" element={<Drift />} />
            <Route path="gate" element={<Gate />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
