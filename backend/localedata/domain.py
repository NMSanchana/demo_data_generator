from enum import Enum

DOMAIN_LIST = [
    "Agriculture & Farming",
    "Manufacturing",
    "Retail & E-commerce",
    "Healthcare",
    "Hospitality & Tourism",
    "Banking & Financial Services",
    "Insurance",
    "Real Estate & Construction",
    "Education",
    "IT & Software Services",
    "Telecommunications",
    "Transportation & Logistics",
    "Automotive",
    "Energy & Utilities",
    "Oil & Gas",
    "Mining",
    "Pharmaceuticals",
    "Media & Entertainment",
    "Government & Public Sector",
    "Non-Profit/NGO",
    "Legal Services",
    "Consulting & Professional Services",
    "Food & Beverage",
    "Textile & Apparel",
    "Aerospace & Defense",
    "Chemicals",
    "FMCG",
]


def _slug(name: str) -> str:
    return (
        name.upper()
        .replace("&", "AND")
        .replace("/", "_")
        .replace("-", "_")
        .replace(" ", "_")
        .replace("__", "_")
    )


DomainEnum = Enum("DomainEnum", {_slug(name): name for name in DOMAIN_LIST})
