#!/usr/bin/env python3
"""image-tags.py, against a made-up registry.

The script holds a build of main back from `latest` and publishes it once it
has been checked. Here skopeo is a stand-in over a registry that is one JSON
file, so the tests can sign, age, break and move images and see what the
script makes of it. The last tests read build.yml, which decides when the
script runs.

    python3 tests/image-tags.py
"""

import contextlib
import datetime
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build.yml"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


script = load("image_tags", ROOT / ".github/workflows/image-tags.py")
script.PAUSE = 0

KDE = "ghcr.io/monolithlinux/kde"
COMMIT = "132c165000000000000000000000000000000000"
DAY = "20261112"
PUBLIC = ["132c165-45", f"{DAY}-45", DAY, "45", "latest"]

# skopeo, as the script uses it. What it does that matters here is what the real one did in a
# trial against a registry with cosign's signatures:
#   - `copy --all` checks the signature of each architecture's image, never the list's;
#   - the image proxy checks the signature of what the tag points at, which is the list;
#   - the proxy leaves without answering Shutdown;
#   - a copy without --remove-signatures writes the signature again;
#   - a tag the registry doesn't have is "manifest unknown".
SKOPEO = r'''#!PYTHON
import hashlib, json, os, socket, sys

path = os.environ["FAKE_REGISTRY"]
argv = sys.argv[1:]
with open(path) as file:
    state = json.load(file)
state["calls"].append(argv)


def save():
    with open(path, "w") as file:
        json.dump(state, file)


def fatal(message, code=1):
    save()
    sys.stderr.write('time="2026-11-12T04:14:31Z" level=fatal msg="%s"\n' % message.replace('"', '\\"'))
    sys.exit(code)


def trouble(what):
    left = state["trouble"].get(what, 0)
    if left > 0:
        state["trouble"][what] = left - 1
    return left != 0


def look_up(reference):
    """(repository, digest) for docker://REPOSITORY:TAG or docker://REPOSITORY@DIGEST; no digest when it isn't there."""
    name = reference.removeprefix("docker://")
    if "@" in name:
        repository, digest = name.split("@")
        return repository, digest if state["images"].get(digest, {}).get("repository") == repository else None
    repository, tag = name.rsplit(":", 1)
    return repository, state["tags"].get(repository, {}).get(tag)


def find(reference):
    repository, digest = look_up(reference)
    if digest is None:
        fatal('Error parsing image name "%s": reading manifest in %s: manifest unknown' % (reference, repository), 2)
    return repository, digest


def rule():
    """The policy and registries.d the call was given, or None when it got none."""
    if "--policy" not in argv:
        return None
    with open(argv[argv.index("--policy") + 1]) as file:
        policy = json.load(file)
    folder = argv[argv.index("--registries.d") + 1] if "--registries.d" in argv else None
    lookaside = {name: open(os.path.join(folder, name)).read() for name in sorted(os.listdir(folder))} if folder else {}
    state["rules"].append({"policy": policy, "registries.d": lookaside})
    return policy, "\n".join(lookaside.values())


def refused(repository, signed, given):
    """Why the rule refuses an image of REPOSITORY, or None. Without a rule skopeo takes anything."""
    if given is None:
        return None
    policy, lookaside = given
    wanted = policy.get("transports", {}).get("docker", {}).get(repository)
    if wanted is None:
        wanted = policy.get("default", [{"type": "reject"}])
    for requirement in wanted:
        if requirement["type"] == "insecureAcceptAnything":
            continue
        if requirement["type"] != "sigstoreSigned":
            return "Running image docker://%s is rejected by policy." % repository
        attached = "%s:\n    use-sigstore-attachments: true" % repository in lookaside
        if not signed or not attached:
            return "A signature was required, but no signature exists"
        if requirement.get("keyPath") != state["key"] or requirement.get("signedIdentity") != {"type": "matchRepository"}:
            return "cryptographic signature verification failed: invalid signature when validating ASN.1 encoded signature"
    return None


if "experimental-image-proxy" in argv:
    given = rule()
    connection = socket.socket(fileno=int(argv[argv.index("--sockfd") + 1]))
    while True:
        request = connection.recv(32 * 1024)
        if not request:
            break
        request = json.loads(request)
        answer = {"success": True, "value": None, "pipeid": 0, "error_code": "", "error": ""}
        if request["method"] == "Initialize":
            answer["value"] = "0.2.8"
        elif request["method"] == "OpenImage":
            if trouble("proxy"):
                break
            repository, digest = look_up(request["args"][0])
            why = ("reading manifest in %s: manifest unknown" % repository if digest is None
                   else refused(repository, state["images"][digest]["list_signed"], given))
            if why:
                answer.update(success=False, error_code="other", error=why)
            else:
                answer["value"] = 1
        elif request["method"] == "Shutdown":
            break
        else:
            answer.update(success=False, error_code="other", error="unknown method: " + request["method"])
        connection.send(json.dumps(answer).encode())
    save()
elif argv and argv[0] == "inspect":
    name = argv[-1].removeprefix("docker://")
    if trouble("read:" + name):
        fatal("pinging container registry ghcr.io: Get \"https://ghcr.io/v2/\": dial tcp: i/o timeout")
    if trouble("unknown:" + name):
        fatal('Error parsing image name "%s": reading manifest in %s: manifest unknown' % (argv[-1], name), 2)
    if trouble("empty:" + name):
        save()
        sys.exit(0)
    repository, digest = find(argv[-1])
    image = state["images"][digest]
    if "--raw" in argv:
        sys.stdout.write(image["manifest"])
    else:
        labels = {"org.opencontainers.image.version": image["version"]} if image["version"] else {}
        json.dump({"Digest": digest, "Created": image["created"], "Labels": labels}, sys.stdout)
    save()
elif "copy" in argv:
    given = rule()
    source, target = argv[-2:]
    repository, digest = find(source)
    why = refused(repository, state["images"][digest]["image_signed"], given)
    if why:
        fatal("copying image 1/1 from manifest list: Source image rejected: " + why)
    there, tag = target.removeprefix("docker://").rsplit(":", 1)
    if trouble("copy:" + tag):
        fatal("writing manifest: uploading manifest %s to %s: received unexpected HTTP status: 503" % (tag, there))
    if "--remove-signatures" not in argv:
        state["signatures written"] += 1
    if "--all" not in argv:
        # Only this computer's image is copied, and the tag gets that image, not the list.
        digest = "sha256:" + hashlib.sha256(("one image of " + digest).encode()).hexdigest()
    if not trouble("lost:" + tag):
        state["tags"].setdefault(there, {})[tag] = digest
    save()
else:
    fatal("the stand-in has no %s" % " ".join(argv), 64)
'''


class Registry:
    """The made-up registry behind the stand-in, and the script's surroundings on a runner."""

    def __init__(self, test):
        self.folder = Path(tempfile.mkdtemp(prefix="image-tags-test-"))
        test.addCleanup(shutil.rmtree, self.folder, True)
        programs = self.folder / "bin"
        programs.mkdir()
        (programs / "skopeo").write_text(SKOPEO.replace("PYTHON", sys.executable))
        (programs / "skopeo").chmod(0o755)
        self.path = self.folder / "registry.json"
        self.output = self.folder / "output"
        self.summary = self.folder / "summary"
        self.state = {"calls": [], "tags": {}, "images": {}, "trouble": {}, "rules": [], "signatures written": 0,
                      "key": str(script.KEY)}
        # Only the stand-in is on PATH: the runner has a real skopeo.
        self.environment = {
            "PATH": str(programs), "FAKE_REGISTRY": str(self.path), "TMPDIR": tempfile.gettempdir(),
            "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": COMMIT,
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_OUTPUT": str(self.output), "GITHUB_STEP_SUMMARY": str(self.summary),
        }

    def image(self, name, repository=KDE, created="2026-11-12T02:17:59Z", version="45.20261111.0",
              list_signed=True, image_signed=True):
        """A build in the registry. Returns its digest."""
        manifest = json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json", "made-up": name})
        digest = "sha256:" + hashlib.sha256(manifest.encode()).hexdigest()
        self.state["images"][digest] = {"repository": repository, "manifest": manifest, "created": created,
                                        "version": version, "list_signed": list_signed, "image_signed": image_signed}
        return digest

    def tag(self, tag, digest, repository=KDE):
        self.state["tags"].setdefault(repository, {})[tag] = digest

    def trouble(self, what, times=-1):
        """Make something go wrong TIMES times (-1: every time). See the stand-in for what can."""
        self.state["trouble"][what] = times

    def run(self, *arguments, **environment):
        """Run image-tags.py. Returns its exit code and what it printed."""
        self.path.write_text(json.dumps(self.state))
        printed = io.StringIO()
        with mock.patch.dict(os.environ, {**self.environment, **environment}, clear=True), \
                contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            code = script.main([str(argument) for argument in arguments])
        self.state = json.loads(self.path.read_text())
        return code, printed.getvalue()

    def tags(self, repository=KDE):
        return dict(self.state["tags"].get(repository, {}))

    def calls(self, command):
        return [call for call in self.state["calls"] if command in call]

    def copied_to(self):
        return [call[-1].rsplit(":", 1)[-1] for call in self.calls("copy")]


class Hold(unittest.TestCase):
    def hold(self, text):
        with tempfile.TemporaryDirectory() as folder:
            recipe = Path(folder, "recipe.yml")
            recipe.write_text(text)
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed), mock.patch.dict(os.environ, {}, clear=True):
                code = script.main(["hold", str(recipe)])
            return code, recipe.read_text(), printed.getvalue()

    def test_a_recipe_gets_candidate_tags(self):
        code, text, _ = self.hold("---\nname: kde\nimage-version: 45\nmodules:\n  - type: signing\n")
        self.assertEqual(code, 0)
        self.assertEqual(text, "---\nname: kde\nimage-version: 45\nmodules:\n  - type: signing\n\nalt-tags:\n  - candidate\n")

    def test_every_recipe_in_the_repository_can_be_held(self):
        recipes = sorted((ROOT / "recipes").glob("recipe-*.yml"))
        self.assertTrue(recipes)
        for recipe in recipes:
            with self.subTest(recipe=recipe.name):
                before = recipe.read_text()
                code, after, _ = self.hold(before)
                self.assertEqual(code, 0)
                # Everything the recipe said is still there, and the new key is a top-level one after it.
                self.assertTrue(after.startswith(before.rstrip() + "\n"))
                self.assertEqual(after[len(before.rstrip()):], "\n\nalt-tags:\n  - candidate\n")

    def test_a_recipe_with_its_own_alt_tags_is_left_alone(self):
        recipe = "name: kde\nalt-tags:\n  - gts\nmodules: []\n"
        code, text, printed = self.hold(recipe)
        self.assertEqual(code, 1)
        self.assertEqual(text, recipe)
        self.assertIn("sets alt-tags itself", printed)

    def test_a_file_that_is_no_recipe_is_left_alone(self):
        code, text, printed = self.hold("modules: []\n")
        self.assertEqual(code, 1)
        self.assertEqual(text, "modules: []\n")
        self.assertIn("has no name", printed)


class Pushed(unittest.TestCase):
    def pushed(self, tag, event="push", entries=None, edition="kde", there=True, **trouble):
        """Run `pushed` after a build that pushed kde:TAG. Returns the registry, exit code and output."""
        registry = Registry(self)
        digest = registry.image("this build")
        if there:
            registry.tag(tag, digest)
        for what, times in trouble.items():
            registry.trouble(f"{what}:{KDE}:{tag}", times)
        digestfile = registry.folder / "pushed.json"
        if entries is not False:
            digestfile.write_text(json.dumps({f"{KDE}:{tag}": digest} if entries is None else entries))
        code, printed = registry.run("pushed", digestfile, edition, GITHUB_EVENT_NAME=event)
        output = registry.output.read_text() if registry.output.exists() else ""
        return registry, digest, code, printed, output

    def test_a_pull_request_build_hands_its_digest_on(self):
        _, digest, code, _, output = self.pushed("pr-70-45", event="pull_request")
        self.assertEqual(code, 0)
        self.assertEqual(output, f"kde={digest}\n")

    def test_a_build_of_main_hands_its_candidate_on(self):
        for event in ("push", "schedule", "workflow_dispatch"):
            with self.subTest(event=event):
                _, digest, code, _, output = self.pushed("candidate", event=event)
                self.assertEqual(code, 0)
                self.assertEqual(output, f"kde={digest}\n")

    def test_a_build_of_another_branch_is_a_candidate_too(self):
        _, digest, code, _, output = self.pushed("br-some_branch-candidate-45", event="workflow_dispatch")
        self.assertEqual(code, 0)
        self.assertEqual(output, f"kde={digest}\n")

    def test_a_build_of_main_that_got_a_public_tag_fails(self):
        for tag in ("latest", DAY, "45", f"{DAY}-45", "132c165-45", "br-main-45", "candidates"):
            with self.subTest(tag=tag):
                _, _, code, printed, output = self.pushed(tag)
                self.assertEqual(code, 1)
                self.assertIn("the public tags were not held back", printed)
                self.assertEqual(output, "")

    def test_the_registry_has_to_have_that_digest_under_that_tag(self):
        registry = Registry(self)
        digest, other = registry.image("this build"), registry.image("another build")
        registry.tag("candidate", other)
        digestfile = registry.folder / "pushed.json"
        digestfile.write_text(json.dumps({f"{KDE}:candidate": digest}))
        code, printed = registry.run("pushed", digestfile, "kde")
        self.assertEqual(code, 1)
        self.assertIn(f"but {KDE}:candidate is {other}", printed)
        self.assertFalse(registry.output.exists())

    def test_a_registry_that_cannot_be_read_is_no_reason_to_build_again(self):
        registry, digest, code, printed, output = self.pushed("candidate", read=-1)
        self.assertEqual(code, 0)
        self.assertIn("::warning::Could not read", printed)
        self.assertEqual(output, f"kde={digest}\n")
        self.assertEqual(len(registry.calls("inspect")), 3)

    def test_one_failed_read_is_tried_again(self):
        _, digest, code, printed, output = self.pushed("candidate", read=1)
        self.assertEqual(code, 0)
        self.assertNotIn("warning", printed)
        self.assertEqual(output, f"kde={digest}\n")

    def test_an_image_of_another_edition_is_refused(self):
        _, _, code, printed, output = self.pushed("candidate", edition="kde-nvidia")
        self.assertEqual(code, 1)
        self.assertIn("not an image of ghcr.io/monolithlinux/kde-nvidia", printed)
        self.assertEqual(output, "")

    def test_anything_but_one_image_and_its_digest_is_refused(self):
        digest = "sha256:" + "a" * 64
        for entries in ({}, [], False, {f"{KDE}:candidate": digest, f"{KDE}:other": digest},
                        {f"{KDE}:candidate": "sha256:abc"}, {f"{KDE}:candidate": None},
                        {f"{KDE}:candidate": digest + "0"}, {KDE: digest}):
            with self.subTest(entries=entries):
                # The registry can't be read, so nothing else stands in the way of a bad entry.
                _, _, code, _, output = self.pushed("candidate", entries=entries, read=-1)
                self.assertEqual(code, 1)
                self.assertEqual(output, "")


class Publish(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(script, "today", lambda: DAY)
        patch.start()
        self.addCleanup(patch.stop)
        self.registry = Registry(self)
        self.old = self.registry.image("yesterday's build", created="2026-11-11T09:41:07Z")
        self.registry.tag("latest", self.old)
        self.registry.tag("45", self.old)
        self.registry.tag("20261111", self.old)

    def build(self, name="this build", **about):
        """A build of COMMIT, pushed the way BlueBuild pushes a held build. Returns its digest."""
        digest = self.registry.image(name, **about)
        fedora = (about.get("version", "45.20261111.0") or "45").split(".")[0]
        for tag in ("candidate", f"candidate-{fedora}", f"{DAY}-candidate-{fedora}", f"132c165-candidate-{fedora}"):
            self.registry.tag(tag, digest)
        return digest

    def publish(self, digest, edition="kde", **environment):
        self.before = self.registry.tags()
        return self.registry.run("publish", edition, digest, **environment)

    def assert_nothing_moved(self):
        self.assertEqual(self.registry.tags(), self.before)

    def test_a_checked_build_gets_the_public_tags_and_latest_last(self):
        digest = self.build()
        code, printed = self.publish(digest)
        self.assertEqual((code, printed.count("is now")), (0, 5))
        self.assertEqual(self.registry.copied_to(), PUBLIC)
        tags = self.registry.tags()
        self.assertEqual({tag: tags[tag] for tag in PUBLIC}, dict.fromkeys(PUBLIC, digest))
        # Yesterday's own tag still names yesterday's build.
        self.assertEqual(tags["20261111"], self.old)
        for call in self.registry.calls("copy"):
            self.assertEqual(call[-2], f"docker://{KDE}@{digest}")
            for option in ("--policy", "--registries.d", "--all", "--preserve-digests", "--remove-signatures"):
                self.assertIn(option, call)
        self.assertEqual(self.registry.state["signatures written"], 0)
        self.assertIn("`latest`, `45`, `20261112`, `20261112-45`, `132c165-45`", self.registry.summary.read_text())
        self.assertIn(digest, self.registry.summary.read_text())

    def test_the_signature_rule_is_the_one_the_image_ships(self):
        self.publish(self.build())
        rules = self.registry.state["rules"]
        # The proxy and each of the five copies got it.
        self.assertEqual(len(rules), 6)
        for rule in rules:
            self.assertEqual(rule["policy"], {
                "default": [{"type": "reject"}],
                "transports": {"docker": {KDE: [{
                    "type": "sigstoreSigned", "keyPath": str(ROOT / "cosign.pub"),
                    "signedIdentity": {"type": "matchRepository"}}]}}})
            self.assertEqual(list(rule["registries.d"].values()),
                             [f"docker:\n  {KDE}:\n    use-sigstore-attachments: true\n"])
        self.assertTrue((ROOT / "cosign.pub").read_text().startswith("-----BEGIN PUBLIC KEY-----"))

    def test_the_proxy_is_asked_before_anything_else(self):
        digest = self.build()
        self.publish(digest)
        first = self.registry.state["calls"][0]
        self.assertIn("experimental-image-proxy", first)
        self.assertIn("--policy", first)

    def test_a_build_that_was_never_signed_is_not_published(self):
        digest = self.build(list_signed=False, image_signed=False)
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn("A Monolith computer would refuse", printed)
        self.assertIn("A signature was required, but no signature exists", printed)
        self.assert_nothing_moved()
        self.assertEqual(self.registry.calls("copy"), [])

    def test_a_build_whose_list_is_not_signed_is_not_published(self):
        # `skopeo copy` alone would publish this one, and no computer would take it.
        digest = self.build(list_signed=False, image_signed=True)
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn("A Monolith computer would refuse", printed)
        self.assert_nothing_moved()
        self.assertEqual(self.registry.calls("copy"), [])

    def test_a_build_whose_image_is_not_signed_is_not_published(self):
        digest = self.build(list_signed=True, image_signed=False)
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn(f"Could not point {KDE}:132c165-45 at {digest}", printed)
        self.assertIn("Source image rejected", printed)
        self.assert_nothing_moved()

    def test_a_build_signed_with_another_key_is_not_published(self):
        digest = self.build()
        self.registry.state["key"] = "/somebody/elses/cosign.pub"
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn("cryptographic signature verification failed", printed)
        self.assert_nothing_moved()

    def test_an_edition_whose_build_did_not_finish_has_nothing_to_publish(self):
        code, printed = self.publish("")
        self.assertEqual(code, 1)
        self.assertIn("The build of kde didn't finish", printed)
        self.assertEqual(self.registry.state["calls"], [])

    def test_only_a_build_of_main_is_published(self):
        digest = self.build()
        for surroundings in ({"GITHUB_REF": "refs/heads/some-branch"}, {"GITHUB_REF": "refs/tags/main"},
                             {"GITHUB_EVENT_NAME": "pull_request"},
                             {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/70/merge"}):
            with self.subTest(surroundings=surroundings):
                code, printed = self.publish(digest, **surroundings)
                self.assertEqual(code, 1)
                self.assertIn("Only builds of main are published", printed)
                self.assertEqual(self.registry.state["calls"], [])

    def test_something_that_is_no_digest_is_refused(self):
        for digest in ("latest", "sha256:abc", "sha256:" + "g" * 64, "sha256:" + "a" * 64 + " --all"):
            with self.subTest(digest=digest):
                code, _ = self.publish(digest)
                self.assertEqual(code, 1)
                self.assertEqual(self.registry.state["calls"], [])

    def test_the_build_has_to_be_the_one_this_commit_pushed(self):
        digest = self.build()
        newer = self.registry.image("tonight's build of the same commit", created="2026-11-12T07:30:00Z")
        self.registry.tag("132c165-candidate-45", newer)
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn("isn't the newest build of 132c165", printed)
        self.assert_nothing_moved()

    def test_a_build_without_this_commits_candidate_tag_is_refused(self):
        digest = self.build()
        del self.registry.state["tags"][KDE]["132c165-candidate-45"]
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn("isn't the newest build of 132c165", printed)
        self.assert_nothing_moved()

    def test_an_older_build_never_replaces_a_newer_one(self):
        digest = self.build(created="2026-11-10T02:00:00Z")
        code, printed = self.publish(digest, GITHUB_RUN_ATTEMPT="2")
        self.assertEqual(code, 1)
        self.assertIn(f"{KDE}:latest is a newer build", printed)
        self.assert_nothing_moved()
        self.assertEqual(self.registry.calls("copy"), [])

    def test_times_are_compared_as_times(self):
        def when(text):
            return script.built({"Created": text}, "an image")

        self.assertEqual(when("2026-11-12T02:17:59Z"), when("2026-11-12T02:17:59.000000000Z"))
        self.assertEqual(when("2026-11-12T02:17:59Z"), when("2026-11-11T20:17:59-06:00"))
        self.assertLess(when("2026-11-12T02:17:59Z"), when("2026-11-12T02:17:59.000001Z"))
        self.assertLess(when("2026-11-12T02:17:59.9Z"), when("2026-11-12T02:18:00Z"))
        self.assertLess(when("2026-11-12T02:17:59.123456789Z"), when("2026-11-12T02:17:59.2Z"))
        self.assertLess(when("2026-11-12T09:00:00Z"), when("2026-11-12T10:00:00+00:00"))
        for text in ("", None, "yesterday", "2026-11-12", "2026-11-12T02:17:59"):
            with self.subTest(text=text), self.assertRaises(script.Problem):
                when(text)

    def test_a_build_that_does_not_say_when_it_was_built_is_refused(self):
        digest = self.build(created="")
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn("doesn't say when it was built", printed)
        self.assert_nothing_moved()

    def test_an_edition_that_has_no_latest_yet_is_published(self):
        del self.registry.state["tags"][KDE]["latest"]
        digest = self.build()
        code, printed = self.publish(digest)
        self.assertEqual(code, 0)
        self.assertIn("has no latest yet", printed)
        self.assertEqual(self.registry.tags()["latest"], digest)

    def test_a_tag_is_only_missing_when_the_registry_says_so_twice(self):
        # One wrong "manifest unknown" for latest must not switch the newer-build check off.
        digest = self.build(created="2026-11-10T02:00:00Z")
        self.registry.trouble(f"unknown:{KDE}:latest", 1)
        code, printed = self.publish(digest, GITHUB_RUN_ATTEMPT="2")
        self.assertEqual(code, 1)
        self.assertIn("is a newer build", printed)
        self.assert_nothing_moved()

    def test_latest_already_being_this_build_is_wrong_the_first_time(self):
        digest = self.build()
        self.registry.tag("latest", digest)
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn("BlueBuild must have pushed the public tags itself", printed)
        self.assert_nothing_moved()

    def test_latest_already_being_this_build_is_fine_on_a_rerun(self):
        digest = self.build()
        self.registry.tag("latest", digest)
        code, printed = self.publish(digest, GITHUB_RUN_ATTEMPT="2")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to do", printed)
        self.assert_nothing_moved()
        self.assertEqual(self.registry.calls("copy"), [])

    def test_a_copy_that_keeps_failing_leaves_latest_alone(self):
        digest = self.build()
        self.registry.trouble("copy:45")
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn(f"Could not point {KDE}:45 at {digest}", printed)
        self.assertIn("503", printed)
        self.assertIn("latest was not moved", printed)
        tags = self.registry.tags()
        self.assertEqual((tags["latest"], tags["45"]), (self.old, self.old))
        self.assertEqual(self.registry.copied_to(), ["132c165-45", f"{DAY}-45", DAY, "45", "45", "45"])

    def test_a_copy_that_fails_once_is_tried_again(self):
        digest = self.build()
        self.registry.trouble("copy:latest", 1)
        code, _ = self.publish(digest)
        self.assertEqual(code, 0)
        self.assertEqual(self.registry.tags()["latest"], digest)

    def test_a_read_that_fails_once_is_tried_again(self):
        digest = self.build()
        self.registry.trouble(f"read:{KDE}:latest", 1)
        self.registry.trouble(f"read:{KDE}@{digest}", 1)
        code, _ = self.publish(digest)
        self.assertEqual(code, 0)
        self.assertEqual(self.registry.tags()["latest"], digest)

    def test_a_registry_that_cannot_be_read_stops_it(self):
        digest = self.build()
        self.registry.trouble(f"read:{KDE}:latest")
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn(f"Could not read {KDE}:latest from the registry", printed)
        self.assertIn("i/o timeout", printed)
        self.assert_nothing_moved()

    def test_an_empty_answer_from_the_registry_is_no_digest(self):
        digest = self.build()
        self.registry.trouble(f"empty:{KDE}:latest")
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn(f"The registry sent an empty manifest for {KDE}:latest", printed)
        self.assert_nothing_moved()

    def test_a_proxy_that_gives_no_answer_once_is_asked_again(self):
        digest = self.build()
        self.registry.trouble("proxy", 1)
        code, _ = self.publish(digest)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.registry.calls("experimental-image-proxy")), 2)

    def test_a_proxy_that_never_answers_stops_it(self):
        digest = self.build()
        self.registry.trouble("proxy")
        code, printed = self.publish(digest)
        self.assertEqual(code, 1)
        self.assertIn("the proxy didn't answer", printed)
        self.assert_nothing_moved()
        self.assertEqual(len(self.registry.calls("experimental-image-proxy")), 3)

    def test_every_tag_is_read_back(self):
        for tag in PUBLIC:
            with self.subTest(tag=tag):
                self.setUp()
                digest = self.build()
                self.registry.trouble(f"lost:{tag}")
                code, printed = self.publish(digest)
                self.assertEqual(code, 1)
                self.assertIn(f"{KDE}:{tag} is {self.before.get(tag, 'missing')} after the copy, not {digest}", printed)

    def test_the_fedora_release_comes_from_the_image(self):
        digest = self.build(version="46.20270420.0")
        code, _ = self.publish(digest)
        self.assertEqual(code, 0)
        self.assertEqual(self.registry.copied_to(), ["132c165-46", f"{DAY}-46", DAY, "46", "latest"])
        # Fedora 45's tag stays on the last Fedora 45 build.
        self.assertEqual(self.registry.tags()["45"], self.old)

    def test_a_build_that_does_not_name_its_fedora_release_is_refused(self):
        for version in ("", "rawhide", ".45"):
            with self.subTest(version=version):
                self.setUp()
                digest = self.build(version=version)
                code, printed = self.publish(digest)
                self.assertEqual(code, 1)
                self.assertIn("doesn't name its Fedora release", printed)
                self.assert_nothing_moved()

    def test_each_edition_has_its_own_repository_and_rule(self):
        nvidia = "ghcr.io/monolithlinux/kde-nvidia"
        digest = self.registry.image("the NVIDIA build", repository=nvidia)
        self.registry.tag("132c165-candidate-45", digest, repository=nvidia)
        code, _ = self.publish(digest, edition="kde-nvidia")
        self.assertEqual(code, 0)
        self.assertEqual(self.registry.tags(nvidia)["latest"], digest)
        self.assert_nothing_moved()
        self.assertEqual({next(iter(rule["policy"]["transports"]["docker"])) for rule in self.registry.state["rules"]},
                         {nvidia})

    def test_another_editions_digest_is_refused(self):
        digest = self.build()
        code, printed = self.publish(digest, edition="kde-nvidia")
        self.assertEqual(code, 1)
        self.assertIn("manifest unknown", printed)
        self.assert_nothing_moved()
        self.assertEqual(self.registry.tags("ghcr.io/monolithlinux/kde-nvidia"), {})

    def test_a_problem_is_one_line_github_shows_on_the_run(self):
        code, printed = self.publish("", GITHUB_ACTIONS="true")
        self.assertEqual(code, 1)
        self.assertTrue(printed.startswith("::error::The build of kde didn't finish"))
        self.assertEqual(printed.count("\n"), 1)


class Day(unittest.TestCase):
    def test_the_date_tags_are_utc_days(self):
        # 8:07 PM on the 11th in Chicago is already the 12th where BlueBuild and the release notes count.
        moment = datetime.datetime(2026, 11, 12, 2, 7, tzinfo=datetime.timezone.utc)
        chicago = datetime.timezone(datetime.timedelta(hours=-6))

        class Clock(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return moment.astimezone(tz) if tz else moment.astimezone(chicago).replace(tzinfo=None)

        with mock.patch.object(script.datetime, "datetime", Clock):
            self.assertEqual(script.today(), "20261112")


def job(name):
    """The lines of one job in build.yml. The file is read by its indentation,
    because the runner's Python has no YAML reader."""
    lines = WORKFLOW.read_text().splitlines()
    start = lines.index(f"  {name}:")
    end = next((number for number in range(start + 1, len(lines))
                if lines[number].startswith("  ") and not lines[number].startswith("   ") and lines[number].strip()
                and not lines[number].lstrip().startswith("#")), len(lines))
    return lines[start:end]


def step(lines, name):
    """One step of a job, up to the next step."""
    start = lines.index(f"      - name: {name}")
    end = next((number for number in range(start + 1, len(lines)) if lines[number].startswith("      - ")), len(lines))
    return lines[start:end]


def step_script(lines, name):
    found = step(lines, name)
    run = found.index("        run: |")
    return "\n".join(line[10:] for line in found[run + 1:]) + "\n"


def editions():
    done = subprocess.run(["bash", str(ROOT / ".github/workflows/pick-editions.sh"), "all"],
                          capture_output=True, text=True, check=True)
    return done.stdout.split()


class Workflow(unittest.TestCase):
    """build.yml decides when the script runs. These read it as text."""

    def test_every_edition_names_itself_and_hands_its_digest_on(self):
        self.assertEqual(editions(), ["kde", "kde-nvidia"])
        check, build = "\n".join(job("check")), job("bluebuild")
        outputs = build[build.index("    outputs:") + 1:build.index("    strategy:")]
        self.assertEqual([line.strip() for line in outputs],
                         [f"{edition}: ${{{{ steps.pushed.outputs.{edition} }}}}" for edition in editions()])
        for edition in editions():
            self.assertIn(f'"edition":"{edition}","recipe":"recipe-{edition}.yml"', check)

    def test_a_build_is_held_unless_it_is_a_pull_request(self):
        build = job("bluebuild")
        hold = step(build, "Hold the public tags back")
        self.assertIn("        if: github.event_name != 'pull_request'", hold)
        self.assertIn('        run: python3 .github/workflows/image-tags.py hold "recipes/${{ matrix.recipe }}"', hold)
        names = [line.strip() for line in build if line.startswith("      - name: ")]
        # Held before the build; the digest is noted after it.
        self.assertLess(names.index("- name: Hold the public tags back"), names.index("- name: Build and push image"))
        self.assertLess(names.index("- name: Build and push image"), names.index("- name: Note which image was pushed"))
        self.assertIn("          build_opts: --digestfile ${{ runner.temp }}/pushed.json", step(build, "Build and push image"))
        noted = step(build, "Note which image was pushed")
        self.assertIn("        id: pushed", noted)
        self.assertNotIn("if:", "\n".join(noted))
        self.assertIn('        run: python3 .github/workflows/image-tags.py pushed "${RUNNER_TEMP}/pushed.json" '
                      '"${{ matrix.edition }}"', noted)

    def test_only_a_tested_build_of_main_is_published(self):
        publish = job("publish")
        self.assertIn("    needs: [test, check, bluebuild]", publish)
        start = publish.index("    if: >-")
        condition = " ".join(line.strip() for line in publish[start + 1:start + 4])
        self.assertEqual(condition, "${{ !cancelled() && github.event_name != 'pull_request' && github.ref == 'refs/heads/main' "
                                    "&& needs.test.result == 'success' && needs.check.result == 'success' "
                                    "&& (needs.bluebuild.result == 'success' || needs.bluebuild.result == 'failure') }}")
        self.assertTrue(publish[start + 4].startswith("    runs-on:"))
        script_lines = step_script(publish, "Check the build and move the public tags to it")
        self.assertIn('python3 .github/workflows/image-tags.py publish "${{ matrix.edition }}" "${DIGEST}"', script_lines)
        self.assertIn("          DIGEST: ${{ needs.bluebuild.outputs[matrix.edition] }}", publish)

    def test_only_the_publish_job_may_write_to_the_registry_besides_the_build(self):
        text = WORKFLOW.read_text()
        self.assertEqual(text.count("packages: write"), 2)
        for name in ("bluebuild", "publish"):
            self.assertIn("      packages: write", job(name))

    def test_the_release_notes_wait_for_a_published_build(self):
        release = job("generate-release")
        self.assertIn("    needs: publish", release)
        self.assertIn("    if: github.event_name != 'pull_request'", release)

    def test_the_tests_run_these_tests(self):
        self.assertIn("        run: python3 tests/image-tags.py", job("test"))

    def result(self, test="success", check="success", build="success", publish="success", on_main="true"):
        """Run the Build result step. Returns its exit code."""
        lines = job("build-result")
        self.assertIn("    needs: [test, check, bluebuild, publish]", lines)
        self.assertIn("    if: always()", lines)
        self.assertIn("          PUBLISH: ${{ needs.publish.result }}", lines)
        self.assertIn("          ON_MAIN: ${{ github.event_name != 'pull_request' && github.ref == 'refs/heads/main' }}", lines)
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "step.sh").write_text(step_script(lines, "Fail unless the tests, check, and every edition build passed"))
            environment = {"PATH": "", "TEST": test, "CHECK": check, "BUILD": build, "PUBLISH": publish, "ON_MAIN": on_main}
            # The shell GitHub runs a step's script with.
            return subprocess.run([shutil.which("bash"), "--noprofile", "--norc", "-e", "-o", "pipefail", "step.sh"],
                                  cwd=folder, env=environment, capture_output=True, text=True).returncode

    def test_build_result_passes_when_everything_did(self):
        self.assertEqual(self.result(), 0)
        # A pull request: nothing is published.
        self.assertEqual(self.result(publish="skipped", on_main="false"), 0)
        self.assertEqual(self.result(build="skipped", publish="skipped", on_main="false"), 0)
        # A night on which nothing had changed: no build, nothing to publish.
        self.assertEqual(self.result(build="skipped", publish="skipped"), 0)

    def test_build_result_fails_when_a_passed_build_of_main_was_not_published(self):
        for publish in ("skipped", "failure", "cancelled"):
            with self.subTest(publish=publish):
                self.assertEqual(self.result(publish=publish), 1)

    def test_build_result_still_fails_for_what_it_failed_for_before(self):
        for failed in ({"test": "failure"}, {"check": "failure"}, {"build": "failure"}, {"build": "cancelled"},
                       {"test": "cancelled"}, {"test": "skipped"}):
            for on_main in ("true", "false"):
                with self.subTest(failed=failed, on_main=on_main):
                    self.assertEqual(self.result(publish="skipped", on_main=on_main, **failed), 1)


if __name__ == "__main__":
    unittest.main()
