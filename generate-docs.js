#!/usr/bin/env node
"use strict";

// Usage: node generate-docs.js <payload.json>
// Payload JSON shape:
//   { job, resume_content, cover_letter, output_dir }
// Outputs two .docx files and prints JSON { resume, cover_letter } to stdout.

const { Document, Packer, Paragraph, TextRun, AlignmentType } = require('docx');
const fs   = require('fs');
const path = require('path');
const os   = require('os');

const payloadPath = process.argv[2];
if (!payloadPath) {
  console.error("Usage: node generate-docs.js <payload.json>");
  process.exit(1);
}

const { job, resume_content, cover_letter, output_dir } = JSON.parse(
  fs.readFileSync(payloadPath, 'utf8')
);

function slug(s) {
  return (s || '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 30);
}

function sectionHeader(title) {
  return new Paragraph({
    children: [new TextRun({ text: title, bold: true, size: 22, color: "1a237e" })],
    thematicBreak: true,
    spacing: { before: 280, after: 80 },
  });
}

// ── Resume ────────────────────────────────────────────────────────────────────
function buildResume() {
  const children = [];

  // Header
  children.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ text: "SARIK ENG", bold: true, size: 36, color: "1a237e" })],
      spacing: { after: 60 },
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({
        text: "Richmond, BC  ·  236-513-1896  ·  sarikc2@gmail.com  ·  Permanent Resident of Canada",
        size: 18, color: "555555",
      })],
      spacing: { after: 200 },
    }),
  );

  // Summary
  children.push(sectionHeader("PROFESSIONAL SUMMARY"));
  children.push(new Paragraph({ text: resume_content.summary || "", spacing: { after: 160 } }));

  // Competencies (two per row)
  children.push(sectionHeader("CORE COMPETENCIES"));
  const comps = resume_content.competencies || [];
  for (let i = 0; i < comps.length; i += 2) {
    const left  = comps[i]     ? `▪  ${comps[i]}` : "";
    const right = comps[i + 1] ? `        ▪  ${comps[i + 1]}` : "";
    children.push(new Paragraph({
      children: [new TextRun({ text: left + right, size: 19 })],
      spacing: { after: 60 },
    }));
  }

  // Experience
  children.push(sectionHeader("PROFESSIONAL EXPERIENCE"));
  for (const block of (resume_content.experience_blocks || [])) {
    children.push(
      new Paragraph({
        children: [new TextRun({ text: block.title_line || "", bold: true, size: 20 })],
        spacing: { before: 140, after: 40 },
      }),
      new Paragraph({
        children: [new TextRun({ text: block.date_range || "", italics: true, size: 19, color: "555555" })],
        spacing: { after: 60 },
      }),
    );
    for (const bullet of (block.bullets || [])) {
      children.push(new Paragraph({
        children: [new TextRun({ text: `•  ${bullet}`, size: 19 })],
        indent: { left: 360 },
        spacing: { after: 60 },
      }));
    }
  }

  // Education
  children.push(sectionHeader("EDUCATION & TRAINING"));
  for (const e of (resume_content.education || [])) {
    children.push(new Paragraph({ text: e, spacing: { after: 80 } }));
  }

  // Certifications
  children.push(sectionHeader("CERTIFICATIONS & FINANCIAL KNOWLEDGE"));
  for (const c of (resume_content.certifications || [])) {
    children.push(new Paragraph({
      children: [new TextRun({ text: `•  ${c}`, size: 19 })],
      indent: { left: 360 },
      spacing: { after: 60 },
    }));
  }

  return new Document({ sections: [{ children }] });
}

// ── Cover Letter ──────────────────────────────────────────────────────────────
function buildCoverLetter() {
  const today = new Date().toLocaleDateString('en-CA', {
    year: 'numeric', month: 'long', day: 'numeric',
  });

  const children = [
    new Paragraph({ text: today, spacing: { after: 280 } }),
    new Paragraph({
      children: [new TextRun({ text: `Hiring Team — ${job.company}`, bold: true })],
      spacing: { after: 60 },
    }),
    new Paragraph({
      children: [new TextRun({ text: `Re: ${job.role}`, italics: true })],
      spacing: { after: 280 },
    }),
  ];

  for (const para of (cover_letter || "").split('\n\n')) {
    if (para.trim()) {
      children.push(new Paragraph({ text: para.trim(), spacing: { after: 200 } }));
    }
  }

  children.push(
    new Paragraph({ text: "Sincerely,", spacing: { before: 280, after: 280 } }),
    new Paragraph({ children: [new TextRun({ text: "Sarik Eng", bold: true })], spacing: { after: 60 } }),
    new Paragraph({ text: "236-513-1896  |  sarikc2@gmail.com" }),
  );

  return new Document({ sections: [{ children }] });
}

// ── Write ─────────────────────────────────────────────────────────────────────
const dir = output_dir || path.join(os.tmpdir(), 'resume-package');
fs.mkdirSync(dir, { recursive: true });

const base       = `${slug(job.company)}_${slug(job.role)}`;
const resumePath = path.join(dir, `Resume_${base}.docx`);
const clPath     = path.join(dir, `CoverLetter_${base}.docx`);

Promise.all([
  Packer.toBuffer(buildResume()).then(buf => fs.writeFileSync(resumePath, buf)),
  Packer.toBuffer(buildCoverLetter()).then(buf => fs.writeFileSync(clPath, buf)),
]).then(() => {
  console.log(JSON.stringify({ resume: resumePath, cover_letter: clPath }));
}).catch(err => {
  console.error("generate-docs error:", err.message);
  process.exit(1);
});
