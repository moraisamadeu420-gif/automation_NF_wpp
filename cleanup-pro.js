const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const emojiRegex = require("emoji-regex")();

const ROOT = process.cwd();

const DRY_RUN = process.argv.includes("--dry");
const APPLY = process.argv.includes("--apply");

function run(cmd) {
  return execSync(cmd, { encoding: "utf8" });
}

// -----------------------------
// 1. REMOVER EMOJIS DE COMMENTS
// -----------------------------
function removeEmojisFromComments(filePath) {
  let content = fs.readFileSync(filePath, "utf8");

  const lines = content.split("\n");

  const cleaned = lines.map((line) => {
    if (
      line.trim().startsWith("//") ||
      line.trim().startsWith("*") ||
      line.trim().startsWith("/*")
    ) {
      return line.replace(emojiRegex, "");
    }
    return line;
  });

  return cleaned.join("\n");
}

// -----------------------------
// 2. REMOVER console.log
// -----------------------------
function removeConsoleLogs(content) {
  return content.replace(/console\.(log|warn|error)\(.*?\);?/g, "");
}

// -----------------------------
// 3. PROCESSAR ARQUIVO
// -----------------------------
function processFile(filePath) {
  let content = fs.readFileSync(filePath, "utf8");

  const original = content;

  content = removeConsoleLogs(content);
  content = removeEmojisFromComments(filePath);

  if (content !== original) {
    if (!DRY_RUN) {
      fs.writeFileSync(filePath, content);
    }
    console.log(`[OK] Limpado: ${filePath}`);
  }
}

// -----------------------------
// 4. VARREDURA DE ARQUIVOS
// -----------------------------
function walk(dir, files = []) {
  const items = fs.readdirSync(dir);

  for (const item of items) {
    const fullPath = path.join(dir, item);

    if (fullPath.includes("node_modules") || fullPath.includes(".git"))
      continue;

    if (fs.statSync(fullPath).isDirectory()) {
      walk(fullPath, files);
    } else if (fullPath.endsWith(".js") || fullPath.endsWith(".ts")) {
      files.push(fullPath);
    }
  }

  return files;
}

// -----------------------------
// 5. KNIP (CÓDIGO MORTO)
// -----------------------------
function runKnip() {
  console.log("\n[ANALISE] Rodando detecção de código morto (knip)...\n");

  try {
    const output = run("npx knip");
    console.log(output);
  } catch {
    console.log("Knip finalizado (verifique saída acima).");
  }
}

// -----------------------------
// 6. DEPCHECK
// -----------------------------
function runDepcheck() {
  console.log("\n[ANALISE] Verificando dependências não usadas...\n");

  try {
    const output = run("npx depcheck");
    console.log(output);
  } catch {
    console.log("Depcheck finalizado.");
  }
}

// -----------------------------
// 7. ESLINT FIX
// -----------------------------
function runLintFix() {
  console.log("\n[FIX] Rodando ESLint auto-fix...\n");

  try {
    run("npx eslint . --fix");
  } catch {
    console.log("ESLint finalizado.");
  }
}

// -----------------------------
// 8. PRETTIER
// -----------------------------
function runPrettier() {
  console.log("\n[FIX] Formatando código com Prettier...\n");

  try {
    run("npx prettier . --write");
  } catch {
    console.log("Prettier finalizado.");
  }
}

// -----------------------------
// MAIN
// -----------------------------
function main() {
  console.log("\n=================================");
  console.log(" CLEANUP PRO - AUDITORIA SÊNIOR ");
  console.log("=================================\n");

  const files = walk(ROOT);

  console.log(`[SCAN] ${files.length} arquivos encontrados\n`);

  for (const file of files) {
    processFile(file);
  }

  runDepcheck();
  runKnip();
  runLintFix();
  runPrettier();

  console.log("\n=================================");
  console.log(" FINALIZADO ");
  console.log("=================================\n");

  if (DRY_RUN) {
    console.log("Modo DRY-RUN: nenhuma alteração foi aplicada.");
  }
}

main();
