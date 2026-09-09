{
  description = "NetAcad offline course exporter";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
    in {
      devShells = nixpkgs.lib.genAttrs systems (system:
        let pkgs = import nixpkgs { inherit system; }; in {
          default = pkgs.mkShell {
            packages = [ (pkgs.python3.withPackages (p: [ p.playwright p.pypdf p.pillow ])) pkgs.chromium ];
            CHROMIUM_PATH = "${pkgs.chromium}/bin/chromium";
            PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";
          };
        });
    };
}
