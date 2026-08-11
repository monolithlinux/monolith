# Monolith maintainer tasks. Run `just` to list.
# The self-managed MOK signs the kernel and out-of-tree modules.

set shell := ["bash", "-euo", "pipefail", "-c"]

export BB_REGISTRY := "ghcr.io"
export BB_REGISTRY_NAMESPACE := "monolithlinux"

_default:
    @just --list

# Build a recipe locally (needs MOK.priv present; run generate-secureboot-key first).
build recipe="recipes/recipe-gnome.yml":
    bluebuild build {{recipe}}

# Generate the private CI secret and public image certificate. Regeneration
# rotates the key and requires every machine to enroll it again.
generate-secureboot-key:
    openssl req -config ./openssl.cnf \
        -new -x509 -newkey rsa:2048 \
        -nodes -days 36500 -outform DER \
        -keyout ./MOK.priv \
        -out ./files/system/etc/pki/akmods/certs/akmods-monolith.der
    @echo
    @echo "Wrote MOK.priv (keep secret) and files/system/.../akmods-monolith.der (commit)."
    @echo "Add MOK.priv as the KERNEL_SIGNING_SECRET GitHub Actions secret, base64-encoded:"
    @echo "  base64 -w0 MOK.priv | gh secret set KERNEL_SIGNING_SECRET -R monolithlinux/monolith"

# Build a live ISO into .iso/ using the same path as Generate ISO.
generate-iso image="ghcr.io/monolithlinux/gnome:latest":
    mkdir -p .iso
    sudo podman build \
        --cap-add sys_admin --security-opt label=disable --squash \
        --build-arg BASE_IMAGE={{image}} \
        -t localhost/monolith-live:latest \
        -f iso/Containerfile iso/
    sudo podman run --rm \
        --cap-add sys_admin --security-opt label=disable \
        -v ./iso/build_iso.sh:/src/build_iso.sh:ro \
        --mount type=image,source=localhost/monolith-live:latest,dst=/rootfs \
        -v ./.iso:/output \
        quay.io/fedora/fedora:latest \
        bash /src/build_iso.sh
