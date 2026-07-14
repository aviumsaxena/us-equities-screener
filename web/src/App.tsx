import { Link, Route, Routes } from "react-router-dom";

import { CompanyPage } from "./pages/CompanyPage";
import { ScreenerPage } from "./pages/ScreenerPage";

export function App() {
  return (
    <div className="app">
      <nav className="topbar">
        <Link className="brand" to="/">
          Screener
        </Link>
        <span className="muted">US equities · fundamentals + prices</span>
      </nav>

      <main>
        <Routes>
          <Route path="/" element={<ScreenerPage />} />
          <Route path="/company/:id" element={<CompanyPage />} />
        </Routes>
      </main>
    </div>
  );
}
