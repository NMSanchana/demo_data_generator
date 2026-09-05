import { useEffect, useState } from "react";
import { useApp } from "../AppContext";
import { generateData, getDomains } from "../api";
import ResultPanel from "./ResultPanel";
import SearchableSelect from "./SearchableSelect";

export default function GeneratePage() {
  const { login } = useApp();

  const [module, setModule] = useState("");
  const [screen, setScreen] = useState("");
  const [domain, setDomain] = useState("");
  const [subdomain, setSubdomain] = useState("");
  const [geography, setGeography] = useState("");
  const [rowCount, setRowCount] = useState(20);

  const [domainOptions, setDomainOptions] = useState([]);
  const [metaError, setMetaError] = useState(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState([]);

  useEffect(() => {
    getDomains()
      .then((res) => setDomainOptions(res.domains.map((d) => ({ value: d, label: d }))))
      .catch((err) => setMetaError(err.message));
  }, []);

  async function handleGenerate(e) {
    e.preventDefault();

    if (!module.trim()) {
      setError("Module is required.");
      return;
    }
    if (!domain.trim()) {
      setError("Domain is required.");
      return;
    }
    if (!geography.trim()) {
      setError("Geography is required — type any location, e.g. a city, state, or country.");
      return;
    }

    setLoading(true);
    setError(null);
    setResults([]);

    const payload = {
      module: module.trim(),
      screen: screen.trim() || null,
      domain: domain.trim(),
      subdomain: subdomain.trim() || null,
      geography: geography.trim(),
      row_count: rowCount ? Number(rowCount) : null,
    };

    try {
      const response = await generateData(login, payload);
      setResults(response.results || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Generate data</h1>
        <p>
          Resolves the screen's real component, extracts its fields, and generates
          realistic demo rows for it — leave Screen blank to generate every screen
          under the module in one go.
        </p>
      </div>

      <form className="panel" onSubmit={handleGenerate}>
        {metaError && (
          <div className="status-banner error">
            Could not load the domain list from the backend: {metaError}
          </div>
        )}

        <div className="field-row">
          <div className="field">
            <label htmlFor="gen-module">Module</label>
            <input
              id="gen-module"
              type="text"
              placeholder="e.g. Skill Management"
              value={module}
              onChange={(e) => setModule(e.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="gen-screen">Screen (optional)</label>
            <input
              id="gen-screen"
              type="text"
              placeholder="e.g. Skill Domain"
              value={screen}
              onChange={(e) => setScreen(e.target.value)}
            />
            <span className="hint">Blank = generate every real screen under this module</span>
          </div>
        </div>

        <div className="field-row">
          <SearchableSelect
            label="Domain"
            placeholder="Search or type any domain..."
            options={domainOptions}
            value={domain}
            onChange={setDomain}
          />
          <div className="field">
            <label htmlFor="gen-subdomain">Subdomain (optional)</label>
            <input
              id="gen-subdomain"
              type="text"
              placeholder="e.g. Organic Farming"
              value={subdomain}
              onChange={(e) => setSubdomain(e.target.value)}
            />
            <span className="hint">Narrows Domain further — free text</span>
          </div>
        </div>

        <div className="field">
          <label htmlFor="gen-geography">Geography</label>
          <input
            id="gen-geography"
            type="text"
            placeholder="Type any location — a locality, city, state, or country (e.g. Singanallur, Tamil Nadu, Japan)"
            value={geography}
            onChange={(e) => setGeography(e.target.value)}
          />
          <span className="hint">
            Doesn't need to be exact — the generator resolves it to a real place
            (e.g. "Singanallur" → Tamil Nadu, India)
          </span>
        </div>

        <div className="field">
          <label htmlFor="gen-row-count">Row count</label>
          <input
            id="gen-row-count"
            type="number"
            min="1"
            max="100"
            value={rowCount}
            onChange={(e) => setRowCount(e.target.value)}
          />
          <span className="hint">Defaults to 20 if left blank</span>
        </div>

        <div className="button-row">
          <button className="btn btn-primary" type="submit" disabled={loading}>
            {loading ? "Generating…" : "Generate"}
          </button>
        </div>

        {error && <div className="status-banner error">{error}</div>}
      </form>

      {results.map((result, i) => (
        <ResultPanel key={`${module}-${result.screen || "all"}-${i}`} module={module} result={result} />
      ))}
    </>
  );
}