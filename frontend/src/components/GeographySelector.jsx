import { useMemo } from "react";
import SearchableSelect from "./SearchableSelect.jsx";

// Encodes every country AND state/region worldwide into one flat,
// searchable list, so typing "Tamil Nadu" jumps straight to the right
// continent + country + state without having to know India comes first
// (the "reverse-searchable, bottom-up" behavior). Selecting a continent or
// country from the cascading dropdowns below achieves the same end state
// the traditional "top-down" way — both paths stay in sync with each other.
function buildUnifiedOptions(tree) {
  const options = [];
  for (const [continentCode, continentEntry] of Object.entries(tree)) {
    for (const country of continentEntry.countries) {
      options.push({
        value: `C:${country.code}`,
        label: country.name,
        continentCode,
        countryCode: country.code,
        stateCode: null,
      });
      for (const state of country.states) {
        options.push({
          value: `S:${state.code}`,
          label: `${state.name} — ${country.name}`,
          continentCode,
          countryCode: country.code,
          stateCode: state.code,
        });
      }
    }
  }
  return options;
}

export default function GeographySelector({ geographyData, value, onChange }) {
  const { continent, country, state } = value;
  const tree = geographyData?.tree || {};

  const continentOptions = useMemo(
    () =>
      Object.entries(tree)
        .map(([code, entry]) => ({ value: code, label: entry.name }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [tree]
  );

  const countryOptions = useMemo(() => {
    if (!continent || !tree[continent]) return [];
    return tree[continent].countries.map((c) => ({ value: c.code, label: c.name }));
  }, [tree, continent]);

  const stateOptions = useMemo(() => {
    if (!continent || !country || !tree[continent]) return [];
    const countryEntry = tree[continent].countries.find((c) => c.code === country);
    return (countryEntry?.states || []).map((s) => ({ value: s.code, label: s.name }));
  }, [tree, continent, country]);

  const unifiedOptions = useMemo(() => buildUnifiedOptions(tree), [tree]);
  const selectedUnifiedValue = state ? `S:${state}` : country ? `C:${country}` : "";

  function handleUnifiedSelect(encodedValue) {
    const match = unifiedOptions.find((o) => o.value === encodedValue);
    if (!match) return;
    onChange({
      continent: match.continentCode,
      country: match.countryCode,
      state: match.stateCode || "",
    });
  }

  function handleContinentSelect(code) {
    onChange({ continent: code, country: "", state: "" });
  }

  function handleCountrySelect(code) {
    onChange({ continent, country: code, state: "" });
  }

  function handleStateSelect(code) {
    onChange({ continent, country, state: code });
  }

  function handleClear() {
    onChange({ continent: "", country: "", state: "" });
  }

  const continentName = continent && tree[continent] ? tree[continent].name : "";
  const countryName = countryOptions.find((c) => c.value === country)?.label || "";
  const stateName = stateOptions.find((s) => s.value === state)?.label || "";

  return (
    <div className="field geography-field">
      <label>Geography (required)</label>

      <SearchableSelect
        placeholder="Quick search any country or state/region worldwide..."
        options={unifiedOptions}
        value={selectedUnifiedValue}
        onChange={handleUnifiedSelect}
      />

      <div className="geo-divider">
        <span>or pick step by step</span>
      </div>

      <div className="geo-cascade">
        <SearchableSelect
          label="Continent"
          placeholder="Select continent..."
          options={continentOptions}
          value={continent}
          onChange={handleContinentSelect}
        />
        <SearchableSelect
          label="Country"
          placeholder={continent ? "Select country..." : "Select a continent first"}
          options={countryOptions}
          value={country}
          onChange={handleCountrySelect}
          disabled={!continent}
        />
        <SearchableSelect
          label="State / Region (optional)"
          placeholder={country ? "Select state/region..." : "Select a country first"}
          options={stateOptions}
          value={state}
          onChange={handleStateSelect}
          disabled={!country || stateOptions.length === 0}
        />
      </div>

      {country && (
        <div className="geo-resolved">
          <span className="geo-chip">{continentName}</span>
          <span className="geo-chip">{countryName}</span>
          {state && <span className="geo-chip geo-chip-strong">{stateName}</span>}
          <button type="button" className="geo-clear" onClick={handleClear}>
            Clear
          </button>
        </div>
      )}
    </div>
  );
}
