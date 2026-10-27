"""Complete MLow activity coding on pinned WaCalls; fail on source drift.

Port of whatsapp-rust PR #1616 (566f8eb), plus the inactive-body reader.
TOC, LSF symbols, pulse geometry and interpolation must agree. No DTX/rate change.
"""
from __future__ import annotations

if __package__:
    from tools.patch_wacalls_voice_lifecycle import replace
else:
    from patch_wacalls_voice_lifecycle import replace


def vad(source: str) -> str:
    source = replace(source, "\tCodedAsActiveVoice bool\n",
                     "\tCodedAsActiveVoice bool\n\tSpeechDetected bool\n")
    source = replace(source, "\tcodedAsActiveVoice := false\n",
                     "\tspeechDetected := false\n\tfor _, v := range vt {\n"
                     "\t\tspeechDetected = speechDetected || v == vadActive\n\t}\n"
                     "\tcodedAsActiveVoice := false\n")
    return replace(source,
                   "return VadPacketResult{VadResults: vadResults, "
                   "CodedAsActiveVoice: codedAsActiveVoice}",
                   "return VadPacketResult{VadResults: vadResults, "
                   "CodedAsActiveVoice: codedAsActiveVoice, SpeechDetected: speechDetected}")


def analysis(source: str) -> str:
    source = replace(source,
                     "\treturn SmplFrameParams{TOC: 0x50, Config: 0, Internal: internal}\n",
                     "\t// Activity follows VAD, not voicing or a constant speech flag.\n"
                     "\ttoc := byte(0x10)\n\tif vad.SpeechDetected { toc |= 0x40 "
                     "} else if codedAsActiveVoice { toc |= 0x02 }\n"
                     "\treturn SmplFrameParams{TOC: toc, Config: 0, Internal: internal}\n")
    source = replace(source, "smplLsfInterpolSearch(brec, fe.prevLsfq, winN)",
                     "smplLsfInterpolSearch(brec, fe.prevLsfq, winN, cs.codedAsActiveVoice)")
    source = replace(source, "func smplLsfInterpolSearch(brec, prevLsfq []float32, winN []float32)",
                     "func smplLsfInterpolSearch(brec, prevLsfq []float32, "
                     "winN []float32, active bool)")
    return replace(source, "\tpc0, res0, rms0 := residualFor(0)\n",
                   "\tpc0, res0, rms0 := residualFor(0)\n"
                   "\t// Inactive packets do not transmit an interpolation index.\n"
                   "\tif !active { return pc0, res0, 0 }\n")


def encoder(source: str) -> str:
    source = replace(source,
                     "config, intf int, lsf *SmplLsfParams) {",
                     "config, intf int, lsf *SmplLsfParams, activity ...bool) {\n"
                     "\tactive := len(activity) == 0 || activity[0]")
    source = replace(source, "\tenc.EncodeCDF(stage1, t.LsfSel[sel])\n",
                     "\tif active { enc.EncodeCDF(stage1, t.LsfSel[sel]) }\n")
    source = replace(source, "\tenc.EncodeCDF(lsf.Extra, t.LsfExtra)\n",
                     "\tif active { enc.EncodeCDF(lsf.Extra, t.LsfExtra) }\n")
    source = replace(source, "\tconst p2, p3, p4 = int32(320), int32(4), int32(1)\n",
                     "\tconst p2, p3 = int32(320), int32(4)\n"
                     "\tactive := ParseSmplTOC(fp.TOC).Active\n"
                     "\tp4 := int32(0)\n\tif active { p4 = 1 }\n"
                     "\tfor _, ip := range fp.Internal {\n"
                     "\t\tif !active && (ip.Lsf.Stage1 != 0 || ip.Lsf.Extra != 0) {\n"
                     '\t\t\treturn nil, errors.New("inactive MLow frame must be unvoiced '
                     'with interpolation zero")\n'
                     "\t\t}\n\t}\n")
    return replace(source, "encodeSmplLsf(enc, tbl, &st, fp.Config, f, &ip.Lsf)",
                   "encodeSmplLsf(enc, tbl, &st, fp.Config, f, &ip.Lsf, active)")


def lsf(source: str) -> str:
    source = replace(source, "\tintf int,\n) SmplLsfIndices {",
                     "\tintf int,\n\tactivity ...bool,\n) SmplLsfIndices {")
    source = replace(source, "\tstage1 := dec.DecodeCDF(t.LsfSel[sel])\n",
                     "\tactive := len(activity) == 0 || activity[0]\n"
                     "\tstage1 := int32(0)\n"
                     "\tif active { stage1 = dec.DecodeCDF(t.LsfSel[sel]) }\n")
    return replace(source, "\tidx.Extra = dec.DecodeCDF(t.LsfExtra)\n",
                   "\tif active { idx.Extra = dec.DecodeCDF(t.LsfExtra) }\n")


def decoder(source: str) -> str:
    # Non-SID inactive frames carry a body and must advance synthesis history.
    source = replace(source, "\tif toc.SID || !toc.Active {\n", "\tif toc.SID {\n")
    source = replace(source, "\tlowRate := (frame[0]>>2)&1 != 0\n",
                     "\tlowRate := (frame[0]>>2)&1 != 0\n"
                     "\tactive := ParseSmplTOC(frame[0]).Active\n"
                     "\tactivity := int32(0)\n\tif active { activity = 1 }\n")
    source = replace(source, "DecodeSmplLsf(dec, tbl, &d.state.Lstate, config, f)",
                     "DecodeSmplLsf(dec, tbl, &d.state.Lstate, config, f, active)")
    return replace(source,
                   "DecodeSmplPulses(dec, mem, SmplIntfLen, 4, 1, int32(config), lsf.Stage1)",
                   "DecodeSmplPulses(dec, mem, SmplIntfLen, 4, activity, "
                   "int32(config), lsf.Stage1)")


TRANSFORMS = {"internal/voip/media/mlow/" + name + ".go": transform
              for name, transform in (("vad", vad), ("analysis", analysis),
                                      ("encoder", encoder), ("lsf", lsf), ("decoder", decoder))}
