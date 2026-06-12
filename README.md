### Description

Script measures RPKI Route Origin Validation in specified country from the v4 or v6 networks of available autonomous systems. Measurements are sourced from [RIPE Atlas probes](https://atlas.ripe.net) and addressed to RPKI-invalid networks provided by [Cloudflare](https://isbgpsafeyet.com/) and [JPNIC](https://rov-check.nic.ad.jp/en).

### Usage overview

```
martin@lab-svr:~$ per-country-rov-analysis.py -h
usage: per-country-rov-analysis.py [-h] (-4 | -6) --country CC --key-file FILENAME [--force] [--graph FILENAME]

Per country RPKI Route Origin Validation check using RIPE Atlas probes

options:
  -h, --help           show this help message and exit
  -4                   perform measurements over IPv4
  -6                   perform measurements over IPv6
  --country CC         ISO 3166-1 alpha-2 country code
  --key-file FILENAME  path to a text file containing the RIPE Atlas API key
  --force              proceed even if > 50 probes are found
  --graph FILENAME     generate a donut chart and save it to the specified filename

Example: per-country-rov-analysis.py -6 --country ee --key-file .ripe-atlas-api-key --graph ee_v6_rov_analysis_05062026.png
martin@lab-svr:~$
```

### Example results



### Acknowledgements

[![ripe ncc logo](https://github.com/tonusoo/per-country-rov-analysis/blob/main/imgs/ripe_ncc_logo.jpg)](https://atlas.ripe.net/)
[![cloudflare logo](https://github.com/tonusoo/per-country-rov-analysis/blob/main/imgs/cloudflare_logo.jpg)](https://isbgpsafeyet.com/)
[![jpnic logo](https://github.com/tonusoo/per-country-rov-analysis/blob/main/imgs/jpnic_logo.jpg)](https://rov-check.nic.ad.jp/en)
