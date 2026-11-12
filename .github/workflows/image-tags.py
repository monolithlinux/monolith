#!/usr/bin/env python3
"""Keep a build of main away from `latest` until it has been checked.

BlueBuild pushes an image under its public tags as soon as it is built, and
signs it afterwards. So `latest` could point at a build whose script tests had
failed, or at one that was never signed and that no Monolith computer accepts.

A build of main is now pushed under candidate tags only, and the Publish job
gives it the public tags once the tests and the build have passed:

    image-tags.py hold RECIPE                before the build
    image-tags.py pushed DIGESTFILE EDITION  after the build
    image-tags.py publish EDITION DIGEST     in the Publish job

hold adds `alt-tags: [candidate]` to the recipe in the runner's checkout, which
makes BlueBuild push `candidate`, `candidate-45`, `20261112-candidate-45` and
`132c165-candidate-45` instead of `latest`, `20261112`, `45`, `20261112-45` and
`132c165-45`. Pull request builds keep their `pr-N-45` tags and skip this.

pushed reads the file BlueBuild's --digestfile wrote and hands the image's
digest to the Publish job. It fails if a build of main got a public tag after
all.

publish takes that digest and gives it the public tags, `latest` last. Before
it moves anything it asks skopeo's image proxy, the program rpm-ostree pulls
through, whether a Monolith computer would accept the image under the
signature rule the image ships. The tags are added to the digest that was
signed, so the signature stays good. It moves nothing when the image is
refused, when it isn't the one this commit's build pushed, or when a newer
build is already published.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "ghcr.io/monolithlinux"
KEY = ROOT / "cosign.pub"  # the image carries the same key as /etc/pki/containers/<edition>.pub
MAIN = "refs/heads/main"
CANDIDATE = "candidate"
VERSION_LABEL = "org.opencontainers.image.version"  # the Fedora base's, such as 45.20261111.0
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TRIES = 3
PAUSE = 5  # seconds between tries


class Problem(Exception):
    """Something that stops the step. The message is for the person reading the run."""


class Missing(Problem):
    """The registry has no such tag."""


def skopeo(*arguments):
    """Run skopeo and return what it printed, or raise Problem with skopeo's reason.

    Reads from the registry fail now and then, so every call gets a few tries.
    A tag the registry doesn't have raises Missing, but only once the registry
    has said so twice: that answer decides whether a newer build exists.
    """
    missing = 0
    error = ""
    for attempt in range(TRIES):
        if attempt:
            time.sleep(PAUSE)
        try:
            result = subprocess.run(["skopeo", *arguments], capture_output=True, check=False)
        except OSError as failure:
            raise Problem(f"skopeo could not be started: {failure}") from failure
        if result.returncode == 0:
            return result.stdout
        error = result.stderr.decode(errors="replace").strip()
        if "manifest unknown" in error:
            missing += 1
            if missing == 2:
                raise Missing("the registry has no such tag")
    # skopeo's last line is: time="..." level=fatal msg="the reason"
    last = error.splitlines()[-1] if error else "skopeo gave no reason"
    message = re.search(r'msg="(.*)"\s*$', last)
    raise Problem(message.group(1).replace('\\"', '"') if message else last)


def digest_of(image, missing_ok=False):
    """The digest IMAGE points at in the registry, from the manifest's own bytes."""
    try:
        manifest = skopeo("inspect", "--raw", f"docker://{image}")
    except Missing:
        if missing_ok:
            return None
        raise Problem(f"The registry has no {image}.") from None
    except Problem as problem:
        raise Problem(f"Could not read {image} from the registry: {problem}") from problem
    if not manifest:
        raise Problem(f"The registry sent an empty manifest for {image}.")
    return "sha256:" + hashlib.sha256(manifest).hexdigest()


def about(image):
    """What skopeo inspect says about IMAGE: its labels and when it was built."""
    try:
        return json.loads(skopeo("inspect", "--no-tags", f"docker://{image}"))
    except (Problem, ValueError) as error:
        raise Problem(f"Could not inspect {image}: {error}") from error


def built(info, image):
    """When the image was built, from skopeo's Created (2026-11-12T02:17:59.123456789Z)."""
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)", str(info.get("Created", "")))
    if not match:
        raise Problem(f"{image} doesn't say when it was built: {info.get('Created')!r}.")
    moment, fraction, zone = match.groups()
    fraction = (fraction or "0")[:6].ljust(6, "0")
    return datetime.datetime.fromisoformat(f"{moment}.{fraction}{'+00:00' if zone == 'Z' else zone}")


def signature_rule(folder, repository):
    """The rule a Monolith computer applies to REPOSITORY, as options for skopeo.

    It repeats /etc/containers/policy.json and registries.d/ from the image: the
    image has to be signed with Monolith's key, and the signature is the one
    cosign attached in the registry. Everything else is refused.
    """
    policy = folder / "policy.json"
    policy.write_text(json.dumps({
        "default": [{"type": "reject"}],
        "transports": {"docker": {repository: [{
            "type": "sigstoreSigned",
            "keyPath": str(KEY),
            "signedIdentity": {"type": "matchRepository"},
        }]}},
    }, indent=2) + "\n")
    registries = folder / "registries.d"
    registries.mkdir()
    (registries / "monolith.yaml").write_text(f"docker:\n  {repository}:\n    use-sigstore-attachments: true\n")
    return ["--policy", str(policy), "--registries.d", str(registries)]


def ask_the_proxy(image, rule):
    """Open IMAGE through skopeo's image proxy. Returns why it was refused, or None.

    rpm-ostree and bootc pull through this proxy, and it applies the signature
    rule to the manifest a tag points at. `skopeo copy --all` applies it to the
    image for each architecture instead: it copies an image whose list was
    never signed, which no computer would take. So both are asked.
    """
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    with ours:
        with theirs:
            try:
                proxy = subprocess.Popen(
                    ["skopeo", *rule, "experimental-image-proxy", "--sockfd", str(theirs.fileno())],
                    pass_fds=[theirs.fileno()], stdin=subprocess.DEVNULL)
            except OSError as failure:
                return f"skopeo could not be started: {failure}"
        ours.settimeout(300)

        def send(method, *arguments):
            ours.send(json.dumps({"method": method, "args": list(arguments)}).encode())

        def call(method, *arguments):
            send(method, *arguments)
            answer = ours.recv(32 * 1024)
            if not answer:
                raise OSError("skopeo closed the connection")
            return json.loads(answer)

        try:
            hello = call("Initialize")
            if not hello.get("success"):
                return f"the proxy didn't start: {hello.get('error')}"
            answer = call("OpenImage", f"docker://{image}")
            if answer.get("success") and answer.get("value"):
                return None
            return str(answer.get("error") or "the proxy gave no reason")
        except (OSError, ValueError) as failure:
            return f"the proxy didn't answer: {failure}"
        finally:
            try:
                send("Shutdown")  # the proxy leaves without answering this one
            except OSError:
                pass
            ours.close()
            try:
                proxy.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait()


def refusal(image, rule):
    """Why a Monolith computer would refuse IMAGE, or None when it takes it."""
    reason = None
    for attempt in range(TRIES):
        if attempt:
            time.sleep(PAUSE)
        reason = ask_the_proxy(image, rule)
        if reason is None:
            return None
    return reason


def today():
    """The day the public date tags get. BlueBuild's own are UTC days as well."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")


def tell(variable, text):
    """Append TEXT to the file GitHub names in VARIABLE (GITHUB_OUTPUT, GITHUB_STEP_SUMMARY)."""
    if os.environ.get(variable):
        with open(os.environ[variable], "a", encoding="utf-8") as file:
            file.write(text + "\n")


def hold(recipe):
    """Give RECIPE candidate tags, so BlueBuild pushes no public tag."""
    text = recipe.read_text()
    if re.search(r"(?m)^alt-tags\s*:", text):
        raise Problem(f"{recipe} sets alt-tags itself. The build adds them; take them out of the recipe.")
    if not re.search(r"(?m)^name\s*:", text):
        raise Problem(f"{recipe} doesn't look like a recipe: it has no name.")
    recipe.write_text(f"{text.rstrip()}\n\nalt-tags:\n  - {CANDIDATE}\n")
    print(f"{recipe}: this build is pushed as {CANDIDATE}, not as latest.")


def pushed(digestfile, edition):
    """Hand the digest BlueBuild pushed to the Publish job, as the job output EDITION."""
    try:
        entries = json.loads(digestfile.read_text())
    except (OSError, ValueError) as error:
        raise Problem(f"BlueBuild left no list of what it pushed in {digestfile}: {error}") from error
    if not isinstance(entries, dict) or len(entries) != 1:
        raise Problem(f"Expected one pushed image in {digestfile}, found {entries!r}.")
    (image, digest), = entries.items()
    repository, _, tag = image.rpartition(":")
    if repository != f"{REGISTRY}/{edition}" or not tag:
        raise Problem(f"BlueBuild pushed {image}, not an image of {REGISTRY}/{edition}.")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise Problem(f"BlueBuild gave {digest!r} as the digest of {image}.")
    held = tag == CANDIDATE or f"-{CANDIDATE}-" in tag
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request" and not held:
        raise Problem(f"BlueBuild pushed {image}: the public tags were not held back. "
                      "Check that alt-tags still does what image-tags.py expects.")
    try:
        there = digest_of(image)
    except Problem as problem:
        # Not worth a rebuild: the Publish job reads the registry again before it moves anything.
        print(f"::warning::Could not read {image} back from the registry: {problem}")
    else:
        if there != digest:
            raise Problem(f"BlueBuild says it pushed {digest}, but {image} is {there}.")
    tell("GITHUB_OUTPUT", f"{edition}={digest}")
    print(f"{edition}: BlueBuild pushed {image} as {digest}.")


def publish(edition, digest):
    """Check the image this run built for EDITION and give it the public tags."""
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request" or os.environ.get("GITHUB_REF") != MAIN:
        raise Problem(f"Only builds of main are published. This is {os.environ.get('GITHUB_EVENT_NAME')} "
                      f"on {os.environ.get('GITHUB_REF')}.")
    repository = f"{REGISTRY}/{edition}"
    if not digest:
        raise Problem(f"The build of {edition} didn't finish, so there is nothing to publish. "
                      f"{repository}:latest stays where it is.")
    if not DIGEST.fullmatch(digest):
        raise Problem(f"{digest!r} is not an image digest.")
    commit = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Problem(f"GITHUB_SHA is {commit!r}, not a commit.")
    candidate = f"{repository}@{digest}"

    with tempfile.TemporaryDirectory(prefix="monolith-image-tags-") as folder:
        rule = signature_rule(Path(folder), repository)
        refused = refusal(candidate, rule)
        if refused is not None:
            raise Problem(f"A Monolith computer would refuse {candidate}: {refused}. "
                          f"{repository}:latest stays where it is.")
        print(f"{candidate} is signed with Monolith's key.")

        info = about(candidate)
        fedora = str((info.get("Labels") or {}).get(VERSION_LABEL, "")).split(".")[0]
        if not fedora.isdigit():
            raise Problem(f"{candidate} doesn't name its Fedora release in {VERSION_LABEL}.")
        ours = f"{repository}:{commit[:7]}-{CANDIDATE}-{fedora}"
        if digest_of(ours, missing_ok=True) != digest:
            raise Problem(f"{ours} is not {digest}, so that isn't the newest build of {commit[:7]}. "
                          "Nothing was changed.")

        latest = digest_of(f"{repository}:latest", missing_ok=True)
        if latest == digest:
            if os.environ.get("GITHUB_RUN_ATTEMPT", "1") == "1":
                raise Problem(f"{repository}:latest is already {digest}, and this job didn't put it there. "
                              "BlueBuild must have pushed the public tags itself.")
            print(f"{repository}:latest is already this build. Nothing to do.")
            return
        if latest is None:
            print(f"{repository} has no latest yet.")
        elif built(about(f"{repository}@{latest}"), f"{repository}:latest") > built(info, candidate):
            raise Problem(f"{repository}:latest is a newer build than {candidate}. Nothing was changed.")

        day = today()
        tags = [f"{commit[:7]}-{fedora}", f"{day}-{fedora}", day, fedora, "latest"]
        for tag in tags:
            # --remove-signatures writes no signature; the ones in the registry belong to the digest
            # and stay as cosign wrote them.
            try:
                skopeo(*rule, "copy", "--all", "--preserve-digests", "--remove-signatures",
                       f"docker://{candidate}", f"docker://{repository}:{tag}")
            except Problem as problem:
                raise Problem(f"Could not point {repository}:{tag} at {digest}: {problem}. "
                              f"{repository}:latest was not moved.") from problem
            print(f"{repository}:{tag} is now {digest}.")
        for tag in tags:
            there = digest_of(f"{repository}:{tag}", missing_ok=True)
            if there != digest:
                raise Problem(f"{repository}:{tag} is {there or 'missing'} after the copy, not {digest}.")

    tell("GITHUB_STEP_SUMMARY", f"Published `{repository}` build `{commit[:7]}-{fedora}` as "
                                f"{', '.join(f'`{tag}`' for tag in reversed(tags))}.\n\n`{digest}`")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hold a build of main back from the public image tags, or publish it.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("hold", help="give a recipe candidate tags")
    command.add_argument("recipe", type=Path)
    command = commands.add_parser("pushed", help="note the image BlueBuild pushed")
    command.add_argument("digestfile", type=Path)
    command.add_argument("edition")
    command = commands.add_parser("publish", help="check an image and give it the public tags")
    command.add_argument("edition")
    command.add_argument("digest")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "hold":
            hold(arguments.recipe)
        elif arguments.command == "pushed":
            pushed(arguments.digestfile, arguments.edition)
        else:
            publish(arguments.edition, arguments.digest)
    except Problem as problem:
        # GitHub shows an ::error:: line on the run's front page.
        print(f"::error::{problem}" if os.environ.get("GITHUB_ACTIONS") else f"image-tags.py: {problem}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
