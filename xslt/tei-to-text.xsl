<?xml version="1.0" encoding="UTF-8"?>
<!--
  tei-to-text.xsl - Plain-text transform: TEI (P5) -> UTF-8 text.

  Purpose: produce clean, indexable reading text for a search engine. It is the
  sibling of tei-to-html.xsl and shares its robustness contract (renders whatever
  subtree it is handed, never assumes a teiHeader).

  What it drops vs. the HTML stylesheet
  =====================================
  * <note> ........... apparatus/footnotes are NOT reading text -> removed whole.
  * editorial markup . <choice> collapses to its reading (corr/expan/reg); the
                       non-reading variant (sic/abbr/orig) is removed.
  * <figure>/<graphic>/<facsimile>/<teiHeader> ... non-textual -> removed.
  * milestones ....... <pb>/<lb>/<cb>/<milestone> emit a single space, not markers.

  Block elements (div, p, ab, l, lg, head, sp, item) end with a newline so each
  citeable line/paragraph is on its own line; inline content is concatenated.
  Whitespace is normalised per block so the index is free of TEI indentation.
-->
<xsl:stylesheet version="3.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:xs="http://www.w3.org/2001/XMLSchema"
    xpath-default-namespace="http://www.tei-c.org/ns/1.0"
    exclude-result-prefixes="xs">

  <xsl:output method="text" encoding="UTF-8"/>

  <xsl:template match="/">
    <xsl:apply-templates select="*"/>
  </xsl:template>

  <!-- Non-textual material: drop entirely. -->
  <xsl:template match="teiHeader | facsimile | note | figure | graphic | fsdDecl"/>

  <!-- Block elements: normalise their text run and terminate with a newline. -->
  <xsl:template match="head | p | ab | l | label">
    <xsl:variable name="run"><xsl:apply-templates/></xsl:variable>
    <xsl:value-of select="normalize-space($run)"/>
    <xsl:text>&#10;</xsl:text>
  </xsl:template>

  <!-- Containers that group blocks: pass through (children add their own newlines). -->
  <xsl:template match="TEI | text | body | group | div | lg | sp | list | item">
    <xsl:apply-templates/>
    <!-- a blank line after a division keeps units visually separable -->
    <xsl:if test="self::div"><xsl:text>&#10;</xsl:text></xsl:if>
  </xsl:template>

  <!-- Editorial apparatus -> reading text only. -->
  <xsl:template match="choice">
    <xsl:variable name="reading" select="(corr, expan, reg, supplied)[1]"/>
    <xsl:choose>
      <xsl:when test="$reading"><xsl:apply-templates select="$reading/node()"/></xsl:when>
      <xsl:otherwise><xsl:apply-templates select="*[1]/node()"/></xsl:otherwise>
    </xsl:choose>
  </xsl:template>
  <!-- When these appear bare (outside <choice>): keep reading variants, drop others. -->
  <xsl:template match="sic | abbr | orig"/>
  <xsl:template match="corr | expan | reg"><xsl:apply-templates/></xsl:template>

  <!-- Milestones collapse to a separating space. -->
  <xsl:template match="pb | lb | cb | milestone | space">
    <xsl:text> </xsl:text>
  </xsl:template>

  <!-- gap is meaningful for search context; render a neutral token. -->
  <xsl:template match="gap"><xsl:text> </xsl:text></xsl:template>

  <!-- Everything else (hi, seg, foreign, w, supplied, unclear, add, ref, persName,
       quote, bibl, g, ...) contributes its text transparently. -->
  <xsl:template match="*"><xsl:apply-templates/></xsl:template>

  <!-- Drop pretty-print indentation between structural blocks, but keep the
       whitespace that separates inline elements inside a block (so adjacent
       words never merge into a single search token). -->
  <xsl:template match="text()[not(normalize-space())]
                       [not(ancestor::p or ancestor::ab or ancestor::l
                            or ancestor::head or ancestor::label or ancestor::item)]"/>
  <xsl:template match="text()"><xsl:value-of select="."/></xsl:template>

</xsl:stylesheet>
