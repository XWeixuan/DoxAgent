# 本轮实际表达式

这里只展开最终本轮候选；各组OR，标题/摘要任一命中即可。正文组仅匹配body。没有全局排除词或隐藏预处理。

## BE

### company · title / summary

```regex
\b(?:Bloom Energy|FuelCell Energy|FCEL)\b
```

### stationary_power_market · title / summary

```regex
\b(?:stationary fuel cells?|solid oxide|SOFC|distributed (?:generation|power|energy)|microgrids?|electroly[sz]ers?|green hydrogen|renewable power)\b
```

### storage_and_grid_market · title / summary

```regex
\b(?:battery (?:energy )?storage|energy storage systems?|grid (?:balancing|edge|modernization)|DERMS)\b
```

### alternative_generation · title / summary

```regex
\b(?:nuclear power|nuclear fusion|nuclear decommissioning|NANO Nuclear|SMRs?)\b
```

### stationary_power_fact · body

```regex
\b(?:(?:on[ -]site|distributed) (?:natural gas )?(?:power|generation)|power generation contracts?|virtual power plants?|electroly[sz]ers?|stationary fuel cells?)\b
```

### compute_facility · title / summary

```regex
\bdata[ -]?cent(?:er|re)s? (?:campus|capacity|construction|expansion|development|pipeline)\b
```

## COHR

### company · title / summary

```regex
\b(?:Coherent Corp(?:oration)?|COHR|MACOM)\b
```

### optical_product_market · title / summary

```regex
\b(?:photonic computing|silicon photonics|optical (?:fiber|fibre|cables?|interconnects?|transceivers?)|data center interconnect|fiber connectivity|industrial lasers?|SiC substrates?)\b
```

### specific_photonic_fact · body

```regex
\b(?:silicon photonics|photonic (?:computing|integrated circuits?|quantum)|optical interconnects?|co-packaged optics|fiber connectivity|specialist lasers?|wavelength (?:services|revenue))\b
```

### compute_facility · title / summary

```regex
\bdata[ -]?cent(?:er|re)s? (?:campus|capacity|construction|expansion|development|pipeline)\b
```

### compute_supply · title / summary

```regex
\b(?:AI compute capacity|GPU clusters?|GPU deployment|bare metal servers?)\b
```

## NBIS

### cloud_provider · title / summary

```regex
\b(?:Nebius|CoreWeave|Runpod|Together AI|Rackspace)\b
```

### compute_service_market · title / summary

```regex
\b(?:GPU (?:cloud|compute|computing)|AI cloud|bare metal|data center services|KV cache)\b
```

### compute_facility · title / summary

```regex
\bdata[ -]?cent(?:er|re)s? (?:campus|capacity|construction|expansion|development|pipeline)\b
```

### facility_dependencies · title / summary

```regex
\bdata center (?:power|cooling|optical fiber|electrical equipment)\b
```

### compute_service_fact · body

```regex
\b(?:GPU cloud|GPU deployment|GPU-accelerated compute|AI cloud revenue|AI cloud infrastructure|hyperscale computing infrastructure|data cent(?:er|re) joint venture|Kubernetes[ -]as[ -]a[ -]service)\b
```

## CEG

### company · title / summary

```regex
\b(?:Constellation Energy|Calpine)\b
```

### nuclear_market · title / summary

```regex
\b(?:nuclear (?:power|energy|reactors?|engineering|fuel|waste|fusion|decommissioning|grade|phase)|spent (?:nuclear )?fuel|uranium|SMRs?)\b
```

### electricity_market · title / summary

```regex
\b(?:power generation|power quality|renewable power|energy storage|battery storage|electric(?:ity)? grid|grid (?:modernization|balancing)|HVDC|net-zero energy buildings)\b
```

### operating_market_fact · body

```regex
\b(?:electricity prices?|power prices?|solar PV projects?|utility-scale (?:solar|energy storage)|battery energy storage systems?)\b
```
