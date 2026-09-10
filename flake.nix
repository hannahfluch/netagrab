{
  description = "netagrab";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachSystem
      [
        "x86_64-linux"
        "aarch64-linux"
      ]
      (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python313;
          project = (fromTOML (builtins.readFile ./pyproject.toml)).project;
          fontConfig = pkgs.makeFontsConf { fontDirectories = [ pkgs.dejavu_fonts ]; };
          netagrab = python.pkgs.buildPythonApplication {
            pname = project.name;
            inherit (project) version;
            pyproject = true;

            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [
                ./src
                ./tests
                ./pyproject.toml
                ./README.md
                ./requirements.txt
                ./uv.lock
              ];
            };

            build-system = [ python.pkgs.hatchling ];
            dependencies = with python.pkgs; [
              beautifulsoup4
              markdownify
              playwright
              pypdf
            ];

            nativeCheckInputs = with python.pkgs; [
              pillow
              pytestCheckHook
            ];
            pytestFlags = [ "tests" ];
            preCheck = ''
              export XDG_CONFIG_HOME="$TMPDIR/chromium-config"
              export XDG_CACHE_HOME="$TMPDIR/chromium-cache"
              mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"
            '';
            pythonImportsCheck = [
              "netagrab.cli"
              "netagrab.exporter"
            ];
            CHROMIUM_PATH = "${pkgs.chromium}/bin/chromium";
            PLAYWRIGHT_NODEJS_PATH = "${pkgs.nodejs}/bin/node";
            FONTCONFIG_FILE = fontConfig;

            makeWrapperArgs = [
              "--set CHROMIUM_PATH ${pkgs.chromium}/bin/chromium"
              "--set PLAYWRIGHT_NODEJS_PATH ${pkgs.nodejs}/bin/node"
              "--set PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD 1"
              "--set-default FONTCONFIG_FILE ${fontConfig}"
            ];

            meta = {
              inherit (project) description;
              mainProgram = "netagrab";
              platforms = pkgs.lib.platforms.linux;
            };
          };
          app = flake-utils.lib.mkApp { drv = netagrab; };
        in
        {
          packages = {
            default = netagrab;
            inherit netagrab;
          };
          apps = {
            default = app;
            netagrab = app;
          };

          devShells.default = pkgs.mkShell {
            packages = [
              python
              pkgs.uv
              pkgs.chromium
              pkgs.nodejs
            ];
            UV_PYTHON = "${python}/bin/python3";
            UV_PYTHON_DOWNLOADS = "never";
            CHROMIUM_PATH = "${pkgs.chromium}/bin/chromium";
            PLAYWRIGHT_NODEJS_PATH = "${pkgs.nodejs}/bin/node";
            PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
              pkgs.stdenv.cc.cc.lib
              pkgs.zlib
            ];
          };
        }
      );
}
