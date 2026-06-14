#!/usr/bin/env python3

"""ROV filtering by country using RIPE Atlas probes.

Script measures RPKI Route Origin Validation in specified
country using RIPE Atlas probes by trying to reach the
targets in RPKI-invalid networks. Targets in RPKI-invalid
networks are from Cloudflare(isbgpsafeyet.com) and
JPNIC(rov-check.nic.ad.jp).

By default, the script logs to stderr and writes results
to stdout in CSV format.

"""

import re
import sys
import time
import asyncio
import logging
import argparse
import datetime
import ipaddress
import aiohttp
import matplotlib.pyplot as plt


logging.basicConfig(
    level=logging.INFO,
    # level=logging.DEBUG,
    format="{asctime} - [{funcName}] - {levelname} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M:%S %z",
    handlers=[logging.StreamHandler(sys.stderr)],
)


def valid_country_code(value):
    """Validate the country code."""

    if not re.match("^[A-Za-z]{2}$", value):
        raise argparse.ArgumentTypeError(
            f'"{value}" is not a valid 2-letter country code.'
        )

    return value.lower()


def parse_arguments():
    """Parses and validates command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Per country RPKI Route Origin Validation check "
        "using RIPE Atlas probes",
        epilog="Example: per-country-rov-analysis.py -6 --country ee "
        "--key-file .ripe-atlas-api-key --graph "
        "ee_v6_rov_analysis_05062026.png",
    )

    ip_group = parser.add_mutually_exclusive_group(required=True)
    ip_group.add_argument(
        "-4",
        dest="ipv4",
        action="store_true",
        help="perform measurements over IPv4",
    )
    ip_group.add_argument(
        "-6",
        dest="ipv6",
        action="store_true",
        help="perform measurements over IPv6",
    )

    parser.add_argument(
        "--country",
        type=valid_country_code,
        required=True,
        metavar="CC",
        help="ISO 3166-1 alpha-2 country code",
    )

    parser.add_argument(
        "--key-file",
        required=True,
        metavar="FILENAME",
        help="path to a text file containing the RIPE Atlas API key",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="proceed even if > 50 probes are found",
    )

    parser.add_argument(
        "--graph",
        default=None,
        metavar="FILENAME",
        help="generate a donut chart and save it to the specified filename",
    )

    args = parser.parse_args()

    try:
        with open(args.key_file, "r", encoding="utf-8") as f:
            api_key = f.read().strip()
            if not api_key:
                parser.error(f'API key file "{args.key_file}" is empty')

    except FileNotFoundError:
        parser.error(f'API key file "{args.key_file}" not found')

    except Exception as err:
        parser.error(f'Error reading API key file "{args.key_file}": {err!r}')

    return args.ipv6, args.country, api_key, args.force, args.graph


def generate_graph(counts, country, is_v6, graph_file):
    """Plot the results to donut chart."""

    labels = ["Validating", "Not Validating", "Inconclusive"]

    sizes = [
        counts.get("validating", 0),
        counts.get("not_validating", 0),
        counts.get("inconclusive", 0),
    ]

    # Green, yellow, gray.
    colors = ["#228B22", "#DAA520", "#808080"]

    filtered_sizes = [size for size in sizes if size > 0]
    filtered_colors = [color for size, color in zip(sizes, colors) if size > 0]
    filtered_labels = [label for size, label in zip(sizes, labels) if size > 0]

    total_asns = sum(filtered_sizes)

    # Avoid overlapping labels. Do not show a label if
    # a slice is very narrow, that is, 5% or below.
    clean_labels = [
        label if (size / total_asns * 100) > 5 else ""
        for label, size in zip(filtered_labels, filtered_sizes)
    ]

    def hide_small_percentages(pct):
        return f"{pct:.0f}%" if pct > 5 else ""

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(
        filtered_sizes,
        labels=clean_labels,
        colors=filtered_colors,
        autopct=hide_small_percentages,
        startangle=90,
        counterclock=False,
        pctdistance=0.8,
        labeldistance=1.05,
        wedgeprops={"width": 0.4, "edgecolor": "white"},
    )

    ax.text(
        0,
        0,
        f"Total ASNs analyzed:\n{total_asns}",
        ha="center",
        va="center",
        fontsize=14,
        fontweight="bold",
    )
    ax.axis("equal")

    fig.suptitle(
        f"ASNs in {country.upper()} or their upstreams performing "
        f"Route Origin Validation for IPv{6 if is_v6 else 4}",
        fontsize=16,
        y=1.0,
    )

    legend_labels = [
        f'{label}: {size} {"ASN" if size == 1 else "ASNs"}'
        for label, size in zip(filtered_labels, filtered_sizes)
    ]

    ax.legend(legend_labels, loc="center left", bbox_to_anchor=(1.2, 0.5))

    current_date = datetime.datetime.now().strftime("%d.%m.%Y")

    fig.text(
        0.5,
        0.02,
        f"Generated on {current_date}",
        ha="center",
        va="bottom",
        fontsize=9,
        color="gray",
        alpha=0.5,
    )

    try:
        plt.savefig(graph_file, bbox_inches="tight")
        logging.info(f'Graph successfully saved to "{graph_file}"')

    except OSError as err:
        logging.error(f'Failed save "{graph_file}": {err!r}')


async def get_probes(session, country, is_v6):
    """Fetch connected probes associated with specified country."""

    url = (
        "https://atlas.ripe.net/api/v2/probes/"
        f"?country_code={country}&status_name=Connected"
    )
    probes = []

    try:
        while url:
            logging.info(f"Fetching {url}")
            async with session.get(url) as resp:

                if resp.status != 200:
                    raise aiohttp.ClientResponseError(
                        request_info=resp.request_info,
                        history=resp.history,
                        status=resp.status,
                    )

                data = await resp.json()
                probes.extend(data.get("results", []))

                # In case of large number of probes, the results are
                # returned on multiple pages. The last page has no
                # "next" key and thus breaks the loop.
                url = data.get("next")

    except aiohttp.ClientError as err:
        logging.error(f"HTTP error: {err!r}")
        raise

    asn_probes = {}

    for probe in probes:
        asn = probe.get("asn_v6") if is_v6 else probe.get("asn_v4")
        prefix = probe.get("prefix_v6") if is_v6 else probe.get("prefix_v4")

        if asn:
            if asn not in asn_probes:
                asn_probes[asn] = []

            logging.info(
                f'Found probe {probe["id"]} in network {prefix} (AS {asn})'
            )

            asn_probes[asn].append(probe["id"])

    return asn_probes


async def get_as_names(session, asns):
    """Fetch the descriptive name of the AS number holder."""

    async def fetch_name(asn):
        url = (
            f"https://stat.ripe.net/data/as-overview/data.json?resource={asn}"
        )
        try:
            async with session.get(url) as resp:

                if resp.status == 200:
                    data = await resp.json()
                    holder = data.get("data", {}).get(
                        "holder", "Unknown AS Name"
                    )
                    return asn, holder

        except aiohttp.ClientError:
            pass

        return asn, "Unknown AS Name"

    tasks = [fetch_name(asn) for asn in asns]
    results = await asyncio.gather(*tasks)

    return dict(results)


async def measure(session, target, af, probe_ids, api_key, sem):
    """Spawns a measurement, polls for completion and fetches results."""

    async with sem:

        url = "https://atlas.ripe.net/api/v2/measurements/"
        headers = {
            "Authorization": f"Key {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # There is a 1k probes per measurement limit. Such a number of
        # probes should never be reached, but stay safe.
        # For example, as of June 2026, Deutsche Telekom (AS 3320) has
        # 373 probes in v4 networks.
        probe_subset = probe_ids[:1000]

        if len(probe_ids) > 1000:
            logging.warning(
                "Number of probes involved in measurement was limited to 1k."
            )

        probes_str = ",".join(map(str, probe_subset))

        # Three ICMP or ICMPv6 "echo request" messages with 1 second
        # interval.
        # According to RIPE Atlas best practices document, it's
        # better to list multiple probe IDs in one item rather than
        # create a separate item for each probe.
        # If the mandatory "requested" parameter is smaller or
        # larger than the number of probes in "probes_str", then
        # this does not seem to have any affect.
        payload = {
            "definitions": [
                {
                    "target": target,
                    "description": f"ROV check {target}",
                    "type": "ping",
                    "af": af,
                    "resolve_on_probe": True,
                    "is_oneoff": True,
                }
            ],
            "probes": [
                {
                    "requested": len(probe_subset),
                    "type": "probes",
                    "value": probes_str,
                }
            ],
        }

        logging.info(
            f"Creating a measurement which will ping {target} "
            f"from probes {probes_str}"
        )

        try:

            if session.closed:
                logging.error("Session was unexpectedly closed.")
                return {}

            async with session.post(
                url, headers=headers, json=payload
            ) as resp:

                if resp.status not in (200, 201, 202):

                    text = await resp.text()
                    logging.error(
                        f"Error creating a measurement for {target} "
                        f"and probes {probes_str}: {text}"
                    )
                    return {}

                data = await resp.json()
                msm_id = data["measurements"][0]

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:

            logging.error(
                f"Network error creating measurement for {target}: "
                f"{err!r}. Skipping."
            )
            return {}

        status_url = f"https://atlas.ripe.net/api/v2/measurements/{msm_id}/"
        start_time = time.monotonic()
        timeout_seconds = 600

        while True:

            if time.monotonic() - start_time > timeout_seconds:
                logging.warning(
                    f"Fetching the measurement status at {status_url} for "
                    f"ping target {target} from {probes_str} timed out."
                )
                return {}

            await asyncio.sleep(5)

            logging.debug(
                f"Checking a measurement status which will ping {target} "
                f"from {probes_str}; {status_url}"
            )

            try:

                if session.closed:
                    logging.error("Session was unexpectedly closed.")
                    return {}

                async with session.get(status_url) as resp:

                    if resp.status == 200:

                        status_data = await resp.json()
                        status_name = status_data["status"]["name"]

                        # The measurement status is often stuck in
                        # "Ongoing"(measurement is running on available
                        # probes) state while the results are actually
                        # in. Stay on the safe side and proceed only
                        # when the measurement status is updated from
                        # "Ongoing".
                        if status_name == "Stopped":
                            logging.info(
                                f"Measurement status for ping target {target} "
                                f"from {probes_str} returned Stopped. Fetch "
                                "the results."
                            )
                            break

                        if status_name in (
                            "Forced to stop",
                            "No suitable probes",
                            "Failed",
                        ):
                            logging.warning(
                                f"Measurement status for ping target {target} "
                                f"from {probes_str} returned {status_name}. "
                                "Skipping results fetch."
                            )
                            return {}

                        logging.debug(
                            f"Measurement status for ping target {target} "
                            f"from {probes_str} returned {status_name}. "
                            "Sleep for 5 seconds and check again."
                        )

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:

                logging.error(
                    f"Network error checking status at {status_url}: "
                    f"{err!r}. Skipping."
                )
                return {}

        results_url = (
            f"https://atlas.ripe.net/api/v2/measurements/{msm_id}/results/"
        )

        try:

            if session.closed:
                logging.error("Session was unexpectedly closed.")
                return {}

            async with session.get(results_url) as resp:

                if resp.status == 200:

                    results = await resp.json()

                    # On rare occasions, the RIPE Atlas API has returned
                    # JSON null for measurement results.
                    if results is None:
                        logging.error(
                            f"Measurement results at {results_url} for ping "
                            f"target {target} from {probes_str} returned null."
                        )
                        return {}

                    parsed_results = {}

                    for result in results:
                        parsed_results[result.get("prb_id")] = result.get(
                            "rcvd", 0
                        )
                        # Measurement result may contain source address
                        # from RIPE Atlas backend system("from" field;
                        # always a public address) and from the probe
                        # itself("src_addr" field; can be a private address
                        # if the probe is behind NAT). Prefer the src
                        # address set by the probe, but fall back to
                        # src address set by the RIPE Atlas backend
                        # system if the probe is behind NAT.

                        src_addr = result.get("src_addr")

                        try:
                            if (
                                src_addr
                                and ipaddress.ip_address(src_addr).is_global
                            ):
                                ip_srt = src_addr
                            else:
                                ip_srt = result.get("from")

                        except ValueError:
                            ip_srt = result.get("from")

                        # On rare occasions, the "src_addr"
                        # has been a private address and
                        # "from" has been an empty string.
                        ip_srt = (ip_srt or "").strip() or "UNKNOWN"

                        logging.info(
                            f'Probe {result.get("prb_id")} with src addr '
                            f'{ip_srt} sent {result.get("sent")} '
                            f'packets to {result.get("dst_name")} and got '
                            f'{result.get("rcvd")} replies'
                        )

                    return parsed_results

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:

            logging.error(
                f"Network error fetching results from {results_url}: "
                f"{err!r}. Skipping."
            )
            return {}

        return {}


async def test_asn(session, asn, probe_ids, af, api_key, sem):
    """Starts the measurements from all the probes of an ASN."""

    # The script uses Cloudflare's https://isbgpsafeyet.com/ and
    # JPNIC's https://rov-check.nic.ad.jp/en ROV checks. There
    # is also https://rpkitest.nlnetlabs.net/ by NLnet Labs, but
    # this tests works by announcing a more-specific RPKI-invalid
    # route and a covering less-specific RPKI-valid route and seems
    # to detect in the backend, which route was followed and signals
    # this back to browser. This test can't be used with RIPE Atlas
    # probes.
    providers = {
        "cloudflare": {
            "valid": "valid.rpki.cloudflare.com",
            "invalid": "invalid.rpki.cloudflare.com",
        },
        "jpnic": {
            "valid": f"v{af}.valid.rov-check.nic.ad.jp",
            "invalid": f"v{af}.invalid.rov-check.nic.ad.jp",
        },
    }

    final_results = {}

    for provider_name, urls in providers.items():

        valid_task = asyncio.create_task(
            measure(session, urls["valid"], af, probe_ids, api_key, sem)
        )
        invalid_task = asyncio.create_task(
            measure(session, urls["invalid"], af, probe_ids, api_key, sem)
        )

        # "valid_res" and "invalid_res" are dictionaries where keys
        # are probe IDs and values are the number of received packets.
        valid_res, invalid_res = await asyncio.gather(valid_task, invalid_task)

        validating = False
        not_validating = False

        for pid in probe_ids:

            valid_rcvd = valid_res.get(pid, 0)
            invalid_rcvd = invalid_res.get(pid, 0)

            # Use the target in RPKI valid prefix as a sanity
            # check, that is, set the conclusive result of
            # validating or not validating only if the target
            # in valid prefix replied.
            if valid_rcvd > 0:
                if invalid_rcvd == 0:
                    validating = True
                else:
                    # Stay on the strict side and categorize
                    # ASN as not validating even if a single
                    # probe reached RPKI-invalid prefix.
                    not_validating = True
                    break

        if not_validating:
            verdict = "not_validating"
        elif validating:
            verdict = "validating"
        else:
            verdict = "inconclusive"

        final_results[provider_name] = verdict

    return asn, final_results


async def summarize_as_results(session, results_list):
    """Summarize the results of ASNs to single result for country."""

    counts = {"validating": 0, "not_validating": 0, "inconclusive": 0}

    asns = [asn for asn, _ in results_list]
    asn_names = await get_as_names(session, asns)

    for asn, providers in results_list:

        statuses = list(providers.values())

        if "not_validating" in statuses:
            verdict = "not_validating"

        # If none of the tests returned "not_validating" and
        # at least one test returned "validating", then set
        # the verdict to "validating".
        elif "validating" in statuses:
            verdict = "validating"

        else:
            verdict = "inconclusive"

        counts[verdict] += 1

        as_name = asn_names.get(asn, "Unknown AS Name")
        for provider_name, provider_status in providers.items():
            logging.info(
                f"AS {asn} ({as_name}); test: {provider_name}; "
                f"result: {provider_status}"
            )

    return counts


async def run_measurements(is_v6, country, api_key, is_force):
    """Calls coroutines which fetch the probes and start tests."""

    async with aiohttp.ClientSession() as session:

        logging.info(
            "Fetching connected RIPE Atlas probes for "
            f"country {country.upper()}"
        )

        try:
            asn_probes = await get_probes(session, country, is_v6)
        except aiohttp.ClientError:
            logging.error("Failed to fetch RIPE Atlas probes")
            sys.exit(1)

        # Sort the probes by AS number starting from the smallest.
        asn_probes = dict(sorted(asn_probes.items()))

        total_probes = sum(len(probe) for probe in asn_probes.values())
        af = 6 if is_v6 else 4

        logging.info(
            f"Found {total_probes} probes in IPv{af} networks "
            f"distributed across {len(asn_probes)} ASNs in "
            f"{country.upper()}"
        )

        if total_probes == 0:
            sys.exit(0)

        if total_probes > 50 and not is_force:
            logging.warning(
                f"{total_probes} probes found. This will consume a "
                "significant amount of credits. Use --force to proceed."
            )
            sys.exit(0)

        # Keep the number of concurrent tasks relatively small in
        # order not to hit the RIPE Atlas limits and keep the load
        # to targets low.
        sem = asyncio.Semaphore(20)

        tasks = []
        for asn, probe_ids in asn_probes.items():

            tasks.append(test_asn(session, asn, probe_ids, af, api_key, sem))

        asn_results = await asyncio.gather(*tasks)

        counts = await summarize_as_results(session, asn_results)

        return counts


def main():
    """Main function calling coroutines and other functions."""

    is_v6, country, api_key, is_force, graph_file = parse_arguments()

    counts = asyncio.run(run_measurements(is_v6, country, api_key, is_force))

    print(
        "country",
        "validating_asns",
        "not_validating_asns",
        "inconclusive_asns",
        "protocol",
        sep=",",
    )
    print(
        country,
        counts.get("validating", 0),
        counts.get("not_validating", 0),
        counts.get("inconclusive", 0),
        "ipv6" if is_v6 else "ipv4",
        sep=",",
    )

    if graph_file:
        generate_graph(counts, country, is_v6, graph_file)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.warning("Interrupted")
        sys.exit(1)
