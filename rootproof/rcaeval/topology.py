"""The Online Boutique call graph used by RCAEval RE2-OB. Callers map to the services they call."""
DEPS = {
    "frontend": ["adservice", "cartservice", "checkoutservice", "currencyservice", "productcatalogservice",
                 "recommendationservice", "shippingservice"],
    "checkoutservice": ["cartservice", "currencyservice", "emailservice", "paymentservice", "productcatalogservice",
                        "shippingservice"],
    "recommendationservice": ["productcatalogservice"],
    "cartservice": ["redis"],
}
SERVICES = ["frontend", "adservice", "cartservice", "checkoutservice", "currencyservice", "emailservice", "paymentservice",
            "productcatalogservice", "recommendationservice", "redis", "shippingservice"]
CALLERS = {s: [p for p, cs in DEPS.items() if s in cs] for s in SERVICES}


def deps_text():
    return "\n".join(f"{caller} -> {', '.join(callees)}" for caller, callees in DEPS.items())
