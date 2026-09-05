import { useEffect, useRef, useState } from "react";

/**
 * Searchable dropdown that also accepts a custom typed value.
 *
 * If what's typed matches an option, selecting it (click or Enter) commits
 * that option's value as usual. If it doesn't match anything and the field
 * loses focus, the typed text itself becomes the value instead of being
 * rejected or reverted -- e.g. typing a domain that isn't in the list
 * still flows through to generation as free text.
 */
export default function SearchableSelect({
  label,
  placeholder,
  options, // [{ value, label }]
  value, // currently selected value (may be a custom string not in options)
  onChange, // (value) => void
  disabled = false,
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);

  const selectedOption = options.find((o) => o.value === value);

  useEffect(() => {
    // Custom values (not present in options) still need to show in the
    // input -- only reset to empty when value itself is empty.
    setQuery(selectedOption ? selectedOption.label : value || "");
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  function commitTypedValue() {
    const trimmed = query.trim();
    if (trimmed && trimmed !== value) {
      onChange(trimmed);
    } else if (!trimmed && value) {
      onChange("");
    }
  }

  useEffect(() => {
    function handleClickOutside(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
        commitTypedValue();
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [query, value]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = query.trim()
    ? options.filter((o) => o.label.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 100)
    : options.slice(0, 100);

  return (
    <div className="field" ref={containerRef}>
      {label && <label>{label}</label>}
      <input
        type="text"
        placeholder={placeholder}
        value={query}
        disabled={disabled}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            setOpen(false);
            commitTypedValue();
            e.target.blur();
          }
          if (e.key === "Escape") {
            setOpen(false);
            e.target.blur();
          }
        }}
      />
      {open && !disabled && (
        <div className="combo-list">
          {filtered.length === 0 && (
            <div className="combo-empty">
              No matches — press Enter to use "{query.trim()}" as typed
            </div>
          )}
          {filtered.map((o) => (
            <div
              key={o.value}
              className={`combo-option${o.value === value ? " active" : ""}`}
              onMouseDown={() => {
                onChange(o.value);
                setQuery(o.label);
                setOpen(false);
              }}
            >
              {o.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}