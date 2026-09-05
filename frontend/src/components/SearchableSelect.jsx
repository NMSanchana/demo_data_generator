import { useEffect, useRef, useState } from "react";

export default function SearchableSelect({
  label,
  placeholder,
  options, // [{ value, label }]
  value, // currently selected value
  onChange, // (value) => void
  disabled = false,
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);

  const selectedOption = options.find((o) => o.value === value);

  useEffect(() => {
    setQuery(selectedOption ? selectedOption.label : "");
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    function handleClickOutside(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
        setQuery(selectedOption ? selectedOption.label : "");
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [selectedOption]);

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
      />
      {open && !disabled && (
        <div className="combo-list">
          {filtered.length === 0 && <div className="combo-empty">No matches</div>}
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
