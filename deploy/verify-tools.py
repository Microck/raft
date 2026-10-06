"""Execute real language programs as the guest developer, without model calls."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from raft import execute, instance, parse_handle  # noqa: E402

PROBE = r"""
import json, os, subprocess, tempfile
from pathlib import Path

checks = {
 "node": ["node", "-e", "console.log('raft-tool-ok')"],
 "npm": ["bash", "-ec", "cd npm-project; npm install --offline --ignore-scripts; npm run proof"],
 "pnpm": ["bash", "-ec", "cd pnpm-project; pnpm install --offline --ignore-scripts; pnpm run proof"],
 "bun": ["bun", "-e", "console.log('raft-tool-ok')"],
 "deno": ["deno", "eval", "console.log('raft-tool-ok')"],
 "python": ["python3", "-c", "import ssl,sqlite3,venv; print('raft-tool-ok')"],
 "uv": ["bash", "-ec", "uv venv --no-project venv; venv/bin/python -c \"print('raft-tool-ok')\""],
 "go": ["bash", "-ec", "go build -o hello-go hello.go; ./hello-go"],
 "rust": ["bash", "-ec", "rustc hello.rs -o hello-rust; ./hello-rust"],
 "cargo": ["cargo", "run", "--offline", "--quiet", "--manifest-path", "cargo-project/Cargo.toml"],
 "java": ["bash", "-ec", "javac Hello.java; java Hello"],
 "maven": ["bash", "-ec", "mvn -q -f maven-project/pom.xml package; java -cp maven-project/target/classes Hello"],
 "gradle": ["gradle", "--no-daemon", "--console=plain", "-p", "gradle-project", "proof"],
 "kotlin": ["bash", "-ec", "kotlinc hello.kt -include-runtime -d hello-kotlin.jar; java -jar hello-kotlin.jar"],
 "scala": ["bash", "-ec", "scalac Hello.scala; scala -nc HelloScala"],
 "ruby": ["ruby", "-e", "puts 'raft-tool-ok'"],
 "bundler": ["bash", "-ec", "export BUNDLE_GEMFILE=$PWD/bundler-project/Gemfile; bundle install --local; bundle exec ruby -e \"puts 'raft-tool-ok'\""],
 "php": ["php", "-r", "echo 'raft-tool-ok';"],
 "composer": ["composer", "--working-dir=composer-project", "run", "proof"],
 "elixir": ["elixir", "-e", 'IO.puts("raft-tool-ok")'],
 "R": ["Rscript", "-e", 'cat("raft-tool-ok")'],
 "gcc": ["bash", "-ec", "gcc hello.c -o hello-c; ./hello-c"],
 "clang": ["bash", "-ec", "clang hello.c -o hello-clang; ./hello-clang"],
 "cpp": ["bash", "-ec", "g++ hello.cpp -o hello-cpp; ./hello-cpp"],
 "cmake-ninja": ["bash", "-ec", "cmake -S . -B build -G Ninja; cmake --build build; build/hello"],
 "dotnet": ["bash", "-ec", "dotnet new console -o dotnet --no-restore; printf 'Console.WriteLine(\"raft-tool-ok\");' > dotnet/Program.cs; mkdir feed; dotnet restore dotnet --source \"$PWD/feed\"; dotnet run --project dotnet --no-restore"],
 "git": ["bash", "-ec", "git init -q repo; git -C repo status --porcelain"],
 "rg": ["rg", "raft-tool-ok", "hello.c"],
 "jq": ["bash", "-ec", "printf '{\"ok\":true}' | jq -e .ok"],
 "ffmpeg": ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.1", "-f", "null", "-"],
 "chromium": ["chromium", "--headless", "--no-sandbox", "--disable-dev-shm-usage", "--dump-dom", "data:text/html,<h1>raft-tool-ok</h1>"],
}
sources = {
 "hello.go": 'package main\nimport "fmt"\nfunc main(){fmt.Println("raft-tool-ok")}\n',
 "hello.rs": 'fn main(){println!("raft-tool-ok");}\n',
 "Hello.java": 'public class Hello {public static void main(String[] args){System.out.println("raft-tool-ok");}}\n',
 "hello.kt": 'fun main(){println("raft-tool-ok")}\n',
 "Hello.scala": 'object HelloScala extends App {println("raft-tool-ok")}\n',
 "hello.c": '#include <stdio.h>\nint main(){puts("raft-tool-ok");return 0;}\n',
 "hello.cpp": '#include <iostream>\nint main(){std::cout << "raft-tool-ok";}\n',
 "npm-project/package.json": json.dumps({"name": "raft-npm-proof", "version": "1.0.0", "scripts": {"proof": "node -e \"console.log('raft-tool-ok')\""}}),
 "pnpm-project/package.json": json.dumps({"name": "raft-pnpm-proof", "version": "1.0.0", "scripts": {"proof": "node -e \"console.log('raft-tool-ok')\""}}),
 "cargo-project/Cargo.toml": '[package]\nname="raft-proof"\nversion="0.1.0"\nedition="2021"\n',
 "cargo-project/src/main.rs": 'fn main(){println!("raft-tool-ok");}\n',
 "maven-project/pom.xml": '<project><modelVersion>4.0.0</modelVersion><groupId>dev.raft</groupId><artifactId>proof</artifactId><version>1</version><properties><maven.compiler.source>17</maven.compiler.source><maven.compiler.target>17</maven.compiler.target></properties></project>',
 "maven-project/src/main/java/Hello.java": 'public class Hello {public static void main(String[] args){System.out.println("raft-tool-ok");}}\n',
 "gradle-project/build.gradle": "apply plugin: 'java'\ntask proof(type: JavaExec) { dependsOn classes; main = 'Hello'; classpath = sourceSets.main.runtimeClasspath }\n",
 "gradle-project/src/main/java/Hello.java": 'public class Hello {public static void main(String[] args){System.out.println("raft-tool-ok");}}\n',
 "bundler-project/Gemfile": 'source "https://rubygems.org"\n',
 "composer-project/composer.json": json.dumps({"name": "raft/proof", "scripts": {"proof": "php -r \"echo 'raft-tool-ok';\""}}),
 "CMakeLists.txt": 'cmake_minimum_required(VERSION 3.10)\nproject(hello C)\nadd_executable(hello hello.c)\n',
}
# These three commands validate state or decode media rather than print the program marker.
proof_tools = checks.keys() - {'git', 'jq', 'ffmpeg'}
report = {}
with tempfile.TemporaryDirectory(prefix='raft-tool-audit-') as work:
 for filename, content in sources.items():
  source = Path(work) / filename
  source.parent.mkdir(parents=True, exist_ok=True)
  source.write_text(content)
 environment = {**os.environ, 'DOTNET_CLI_TELEMETRY_OPTOUT': '1', 'DOTNET_NOLOGO': '1'}
 for tool, command in checks.items():
  try:
   result = subprocess.run(command, cwd=work, env=environment, capture_output=True, text=True, timeout=120)
   report[tool] = {'passed': result.returncode == 0 and (tool not in proof_tools or 'raft-tool-ok' in result.stdout), 'exitCode': result.returncode,
                   'output': (result.stdout + result.stderr)[-2000:]}
  except (OSError, subprocess.TimeoutExpired) as error:
   report[tool] = {'passed': False, 'output': str(error)}
print(json.dumps(report))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("box")
    args = parser.parse_args()
    location, name = parse_handle(args.box)
    instance(location, name)
    response = execute(
        location, name, ["runuser", "-u", "developer", "--", "python3", "-c", PROBE], capture=True
    )
    if response.returncode:
        raise RuntimeError(response.stderr.decode().strip())
    report = json.loads(response.stdout)
    print(json.dumps(report, indent=2))
    return int(any(not check["passed"] for check in report.values()))


if __name__ == "__main__":
    sys.exit(main())
