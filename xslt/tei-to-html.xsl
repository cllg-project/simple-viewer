<?xml version="1.0" encoding="UTF-8"?>
<!--
  tei-to-html.xsl - Presentation transform: TEI (P5) -> HTML.

  Design goals
  ============
  * ROBUST TO PARTIAL INPUT. The corpus is served as excerpts: dapytains hands
    us whatever subtree a citation resolves to (a whole <TEI>, or a reconstructed
    <TEI><text><body>...only one <div>...</body></text></TEI>, or in principle a
    bare <div>/<l>). We never assume we can see the whole document or a teiHeader.
    The entry template just walks the top element, whatever it is, and a low
    priority catch-all renders any element we do not explicitly know about, so
    nothing is ever silently dropped.
  * Output is an HTML *fragment* (no <html>/<body> wrapper) so the browse app can
    drop it inside its own page. method="html", indentation off to preserve the
    significant whitespace in verse.
  * Every construct carries a stable `tei-*` class documented in xslt/tei.css.

  See xslt/README.md for the full class table and the HTML output description.
-->
<xsl:stylesheet version="3.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:xs="http://www.w3.org/2001/XMLSchema"
    xpath-default-namespace="http://www.tei-c.org/ns/1.0"
    exclude-result-prefixes="xs">

  <xsl:output method="html" encoding="UTF-8" omit-xml-declaration="yes" indent="no"/>
  <xsl:strip-space elements="div lg sp list TEI text body"/>
  <xsl:preserve-space elements="l p ab head label seg"/>

  <!-- ============================================================= -->
  <!-- Entry point: render whatever top element(s) we were handed.    -->
  <!-- Works for a full <TEI> or a bare passage subtree alike.        -->
  <!-- ============================================================= -->
  <xsl:template match="/">
    <xsl:apply-templates select="*"/>
  </xsl:template>

  <!-- Metadata is never presented as reading text. -->
  <xsl:template match="teiHeader | facsimile | fsdDecl"/>

  <!-- Transparent containers: just recurse. -->
  <xsl:template match="TEI | text | body | group">
    <xsl:apply-templates/>
  </xsl:template>

  <!-- ============================================================= -->
  <!-- Block-level structure                                          -->
  <!-- ============================================================= -->

  <!-- Structural divisions. @type/@n exposed as data-* for styling/JS. -->
  <xsl:template match="div">
    <section class="tei-div">
      <xsl:if test="@type"><xsl:attribute name="data-type" select="@type"/></xsl:if>
      <xsl:if test="@subtype"><xsl:attribute name="data-subtype" select="@subtype"/></xsl:if>
      <xsl:if test="@n">
        <xsl:attribute name="data-n" select="@n"/>
        <!-- A visible reference tag when the div is a citeable unit with no <head>. -->
        <xsl:if test="not(head)">
          <span class="tei-ref-n"><xsl:value-of select="@n"/></span>
        </xsl:if>
      </xsl:if>
      <xsl:apply-templates/>
    </section>
  </xsl:template>

  <!-- Headings: HTML level follows the div nesting depth (h2..h6). -->
  <xsl:template match="head">
    <xsl:variable name="depth" select="count(ancestor::div)"/>
    <xsl:variable name="tag" select="'h' || string(min((max(($depth + 1, 2)), 6)))"/>
    <xsl:element name="{$tag}">
      <xsl:attribute name="class">tei-head</xsl:attribute>
      <xsl:apply-templates/>
    </xsl:element>
  </xsl:template>

  <!-- A <label type="head"> is a running header inside content, not a div title. -->
  <xsl:template match="label[@type='head']">
    <span class="tei-head-label"><xsl:apply-templates/></span>
  </xsl:template>
  <xsl:template match="label">
    <span class="tei-label">
      <xsl:if test="@type"><xsl:attribute name="data-type" select="@type"/></xsl:if>
      <xsl:apply-templates/>
    </span>
  </xsl:template>

  <xsl:template match="p">
    <p class="tei-p"><xsl:apply-templates/></p>
  </xsl:template>

  <!-- <ab> (anonymous block) behaves like a paragraph for display. -->
  <xsl:template match="ab">
    <p class="tei-ab"><xsl:apply-templates/></p>
  </xsl:template>

  <!-- Verse -->
  <xsl:template match="lg">
    <div class="tei-lg">
      <xsl:if test="@type"><xsl:attribute name="data-type" select="@type"/></xsl:if>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="l">
    <div class="tei-l">
      <xsl:if test="@n">
        <xsl:attribute name="data-n" select="@n"/>
        <span class="tei-lineno"><xsl:value-of select="@n"/></span>
      </xsl:if>
      <span class="tei-l-text"><xsl:apply-templates/></span>
    </div>
  </xsl:template>

  <!-- Speeches -->
  <xsl:template match="sp">
    <div class="tei-sp"><xsl:apply-templates/></div>
  </xsl:template>
  <xsl:template match="speaker">
    <span class="tei-speaker"><xsl:apply-templates/></span>
  </xsl:template>

  <!-- Lists -->
  <xsl:template match="list">
    <ul class="tei-list"><xsl:apply-templates/></ul>
  </xsl:template>
  <xsl:template match="item">
    <li class="tei-item"><xsl:apply-templates/></li>
  </xsl:template>

  <!-- Figures -->
  <xsl:template match="figure">
    <figure class="tei-figure"><xsl:apply-templates/></figure>
  </xsl:template>
  <xsl:template match="graphic">
    <img class="tei-graphic">
      <xsl:if test="@url"><xsl:attribute name="src" select="@url"/></xsl:if>
    </img>
  </xsl:template>
  <xsl:template match="figDesc">
    <figcaption class="tei-figdesc"><xsl:apply-templates/></figcaption>
  </xsl:template>

  <!-- ============================================================= -->
  <!-- Inline phrase-level                                            -->
  <!-- ============================================================= -->

  <!-- Highlight: map common @rend values to semantic HTML, fall back to a span. -->
  <xsl:template match="hi[matches(@rend,'(^|\s)(italic|i|ital)($|\s)')]">
    <em class="tei-hi"><xsl:apply-templates/></em>
  </xsl:template>
  <xsl:template match="hi[matches(@rend,'(^|\s)(bold|b)($|\s)')]">
    <strong class="tei-hi"><xsl:apply-templates/></strong>
  </xsl:template>
  <xsl:template match="hi[matches(@rend,'(^|\s)(sup|superscript)($|\s)')]">
    <sup class="tei-hi"><xsl:apply-templates/></sup>
  </xsl:template>
  <xsl:template match="hi[matches(@rend,'(^|\s)(sub|subscript)($|\s)')]">
    <sub class="tei-hi"><xsl:apply-templates/></sub>
  </xsl:template>
  <xsl:template match="hi">
    <span class="tei-hi">
      <xsl:if test="@rend">
        <xsl:attribute name="class">tei-hi tei-rend-<xsl:value-of select="replace(normalize-space(@rend),'\s+','-')"/></xsl:attribute>
      </xsl:if>
      <xsl:apply-templates/>
    </span>
  </xsl:template>

  <!-- Editorial apparatus: present the *reading* text, keep the variant in @title. -->
  <xsl:template match="choice">
    <xsl:variable name="reading" select="(corr, expan, reg, supplied)[1]"/>
    <xsl:variable name="variant" select="(sic, abbr, orig)[1]"/>
    <span class="tei-choice">
      <xsl:if test="$variant">
        <xsl:attribute name="title" select="normalize-space(string($variant))"/>
      </xsl:if>
      <xsl:choose>
        <xsl:when test="$reading"><xsl:apply-templates select="$reading/node()"/></xsl:when>
        <xsl:otherwise><xsl:apply-templates select="*[1]/node()"/></xsl:otherwise>
      </xsl:choose>
    </span>
  </xsl:template>
  <!-- Bare (non-choice) apparatus elements still render their text. -->
  <xsl:template match="corr | expan | reg">
    <span class="tei-{local-name()}"><xsl:apply-templates/></span>
  </xsl:template>
  <xsl:template match="sic | abbr | orig">
    <span class="tei-{local-name()}"><xsl:apply-templates/></span>
  </xsl:template>

  <xsl:template match="foreign">
    <span class="tei-foreign">
      <xsl:if test="@xml:lang"><xsl:attribute name="lang" select="@xml:lang"/></xsl:if>
      <xsl:apply-templates/>
    </span>
  </xsl:template>

  <!-- Lacunae & editorial intervention -->
  <xsl:template match="gap">
    <span class="tei-gap" title="{normalize-space(string-join((@reason, @extent, @unit), ' '))}">[…]</span>
  </xsl:template>
  <xsl:template match="supplied">
    <span class="tei-supplied">&#x2329;<xsl:apply-templates/>&#x232A;</span>
  </xsl:template>
  <xsl:template match="unclear">
    <span class="tei-unclear"><xsl:apply-templates/></span>
  </xsl:template>
  <xsl:template match="del">
    <del class="tei-del"><xsl:apply-templates/></del>
  </xsl:template>
  <xsl:template match="add">
    <ins class="tei-add"><xsl:apply-templates/></ins>
  </xsl:template>

  <xsl:template match="seg">
    <span class="tei-seg">
      <xsl:if test="@type"><xsl:attribute name="data-type" select="@type"/></xsl:if>
      <xsl:apply-templates/>
    </span>
  </xsl:template>
  <xsl:template match="w">
    <span class="tei-w"><xsl:apply-templates/></span>
  </xsl:template>
  <xsl:template match="g">
    <span class="tei-g"><xsl:apply-templates/></span>
  </xsl:template>

  <xsl:template match="quote">
    <q class="tei-quote"><xsl:apply-templates/></q>
  </xsl:template>
  <xsl:template match="q">
    <span class="tei-q"><xsl:apply-templates/></span>
  </xsl:template>
  <xsl:template match="cit">
    <span class="tei-cit"><xsl:apply-templates/></span>
  </xsl:template>
  <xsl:template match="bibl">
    <span class="tei-bibl"><xsl:apply-templates/></span>
  </xsl:template>

  <xsl:template match="ref">
    <xsl:choose>
      <xsl:when test="@target">
        <a class="tei-ref" href="{@target}"><xsl:apply-templates/></a>
      </xsl:when>
      <xsl:otherwise>
        <span class="tei-ref"><xsl:apply-templates/></span>
      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <!-- Notes are kept in the HTML (CSS folds them to a hoverable marker); the
       plain-text variant drops them instead. -->
  <xsl:template match="note">
    <span class="tei-note">
      <xsl:if test="@type"><xsl:attribute name="data-type" select="@type"/></xsl:if>
      <xsl:if test="@n"><xsl:attribute name="data-n" select="@n"/></xsl:if>
      <span class="tei-note-body"><xsl:apply-templates/></span>
    </span>
  </xsl:template>

  <!-- Milestones (boundary markers, no content) -->
  <xsl:template match="pb">
    <span class="tei-pb" title="page {@n}">
      <xsl:if test="@n"><xsl:attribute name="data-n" select="@n"/></xsl:if>
    </span>
  </xsl:template>
  <xsl:template match="cb">
    <span class="tei-cb">
      <xsl:if test="@n"><xsl:attribute name="data-n" select="@n"/></xsl:if>
    </span>
  </xsl:template>
  <xsl:template match="lb">
    <br class="tei-lb"/>
  </xsl:template>
  <xsl:template match="milestone">
    <span class="tei-milestone">
      <xsl:if test="@unit"><xsl:attribute name="data-unit" select="@unit"/></xsl:if>
      <xsl:if test="@n"><xsl:attribute name="data-n" select="@n"/></xsl:if>
    </span>
  </xsl:template>
  <xsl:template match="space">
    <span class="tei-space">
      <xsl:value-of select="string-join((for $i in 1 to xs:integer((@quantity, '1')[1]) return '&#160;'), '')"/>
    </span>
  </xsl:template>

  <!-- Named entities -->
  <xsl:template match="persName | placeName | name | author | editor | title | date | num">
    <span class="tei-{local-name()}"><xsl:apply-templates/></span>
  </xsl:template>

  <!-- ============================================================= -->
  <!-- Catch-all: any TEI element we did not name above still renders  -->
  <!-- its content, tagged with tei + tei-<localname> for inspection.  -->
  <!-- This is what makes the stylesheet robust across both corpora.   -->
  <!-- ============================================================= -->
  <xsl:template match="*" priority="-1">
    <span class="tei tei-{local-name()}"><xsl:apply-templates/></span>
  </xsl:template>

  <!-- Text nodes copied verbatim (significant verse whitespace preserved). -->
  <xsl:template match="text()">
    <xsl:value-of select="."/>
  </xsl:template>

</xsl:stylesheet>
