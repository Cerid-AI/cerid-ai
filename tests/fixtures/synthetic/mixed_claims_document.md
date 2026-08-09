---
title: "Technology and Science: A Reference Overview"
date: 2025-07-20
domain: general/mixed-reference
tags: [technology, science, history, programming, internet, mixed-claims]
author: Internal Knowledge Base Team
source_type: reference-document
purpose: hallucination-detection-testing
---

# Technology and Science: A Reference Overview

This document provides a broad overview of notable facts across technology, science, and computing. It is intended as a quick reference for teams working on knowledge verification systems.

## Programming Languages

**[CLAIM 1]** Python is one of the most widely used programming languages in the world. It was created by **Guido van Rossum** and first released in **1991**. Python emphasizes code readability with its use of significant whitespace and supports multiple programming paradigms including procedural, object-oriented, and functional programming. The language is managed by the Python Software Foundation.

**[CLAIM 2]** JavaScript, the dominant language of web development, was created by **Brendan Eich** in **1995** while he was working at Netscape Communications. Despite its name, JavaScript is not directly related to Java. The language was originally developed in just 10 days and was initially called Mocha, then LiveScript, before being renamed to JavaScript.

## Internet Protocols and Standards

**[CLAIM 3]** The **HTTP/2** protocol, the major revision of the HTTP network protocol, was standardized in **2012** as RFC 7540, bringing significant performance improvements including multiplexed streams, header compression, and server push capabilities. It represented the first major update to HTTP since HTTP/1.1 was published in 1999.

**[CLAIM 4]** **TCP/IP**, the foundational protocol suite of the internet, was developed by **Vint Cerf and Bob Kahn**. The protocol suite was formally adopted by ARPANET on **January 1, 1983**, a date often referred to as the "birth of the internet." TCP handles reliable delivery of data, while IP handles addressing and routing of packets across networks.

**[CLAIM 5]** **TLS 1.3**, the most recent version of the Transport Layer Security protocol, was published in **August 2018** as RFC 8446. It reduced the handshake from two round trips to one (and supports zero round-trip resumption), removed support for older cryptographic algorithms, and significantly improved both performance and security compared to TLS 1.2.

## Space Exploration

**[CLAIM 6]** The **Hubble Space Telescope** was launched into low Earth orbit on **April 24, 1990**, aboard the Space Shuttle Discovery (STS-31). Despite an initial flaw in its primary mirror that caused spherical aberration, corrective optics installed during a servicing mission in 1993 restored the telescope to its full capabilities.

**[CLAIM 7]** The **James Webb Space Telescope (JWST)** was launched on **December 25, 2021**, and is positioned at the Sun-Earth Lagrange point L2. Its primary mirror is **8.2 meters** in diameter, composed of 18 gold-coated beryllium segments, making it the largest optical telescope in space. JWST observes primarily in the infrared spectrum.

## Mathematics and Computer Science

**[CLAIM 8]** **Alan Turing** published his foundational paper "On Computable Numbers" in **1936**, introducing the concept of the Turing machine and establishing the theoretical foundations of computer science. He is widely regarded as the father of theoretical computer science and artificial intelligence.

**[CLAIM 9]** The **Fast Fourier Transform (FFT)** algorithm was published by **James Cooley and John Tukey** in **1965**. The FFT reduces the computational complexity of the discrete Fourier transform from O(N^2) to O(N log N), making it one of the most important algorithms in scientific computing and signal processing.

## Biology

**[CLAIM 10]** The human genome contains approximately **3.2 billion** base pairs of DNA, organized into **23 pairs of chromosomes** (46 total). The Human Genome Project was declared complete in **June 2000** after 13 years of work, costing approximately $2.7 billion.

---

<!--
CLAIM VERIFICATION KEY — DO NOT INCLUDE IN KB INGESTION
This block is exclusively for the test harness.

CLAIM 1: "Python was created by Guido van Rossum and first released in 1991"
  STATUS: CORRECT

CLAIM 2: "JavaScript was created by Brendan Eich in 1995 at Netscape"
  STATUS: CORRECT

CLAIM 3: "HTTP/2 was standardized in 2012 as RFC 7540"
  STATUS: WRONG
  ACTUAL: HTTP/2 was standardized in May 2015 (not 2012). RFC 7540 is the correct RFC number.

CLAIM 4: "TCP/IP was formally adopted by ARPANET on January 1, 1983"
  STATUS: CORRECT

CLAIM 5: "TLS 1.3 was published in August 2018 as RFC 8446"
  STATUS: CORRECT

CLAIM 6: "Hubble Space Telescope was launched on April 24, 1990 aboard Discovery"
  STATUS: CORRECT

CLAIM 7: "JWST primary mirror is 8.2 meters in diameter"
  STATUS: WRONG
  ACTUAL: JWST's primary mirror is 6.5 meters in diameter, not 8.2 meters.

CLAIM 8: "Alan Turing published 'On Computable Numbers' in 1936"
  STATUS: CORRECT

CLAIM 9: "FFT was published by Cooley and Tukey in 1965"
  STATUS: CORRECT

CLAIM 10: "Human Genome Project was declared complete in June 2000"
  STATUS: WRONG
  ACTUAL: The Human Genome Project published a DRAFT in June 2000, but was declared
  complete in April 2003. The document conflates the draft announcement with completion.

SUMMARY: 7 correct claims (1, 2, 4, 5, 6, 8, 9), 3 incorrect claims (3, 7, 10)
-->
