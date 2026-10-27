"""Instrument the pinned media stages without changing codec or scheduling policy."""
from __future__ import annotations

if __package__:
    from tools.patch_wacalls_voice_lifecycle import replace
else:
    from patch_wacalls_voice_lifecycle import replace


def manager(source: str) -> str:
    return replace(source, "\tOnPeerAudio   func([]float32)\n",
                   "\tOnPeerAudio   func([]float32)\n\tOnMediaEvent func(MediaEvent)\n")


def media(source: str) -> str:
    source = replace(source, "\tm.lastCaptureAt = time.Now()\n",
                     '\tm.canObserve(MediaEvent{Kind:"capture", Samples:len(data)})\n'
                     "\tm.lastCaptureAt = time.Now()\n")
    source = replace(source,
                     "\tif m.codec == nil || m.rtpSession == nil || m.srtpSession == nil || "
                     "!m.relay.HasConnection() {\n\t\treturn\n",
                     "\tif m.codec == nil || m.rtpSession == nil || m.srtpSession == nil || "
                     "!m.relay.HasConnection() {\n"
                     '\t\tm.canObserve(MediaEvent{Kind:"capture_not_ready", '
                     'Samples:len(data)})\n\t\treturn\n')
    source = replace(source, "\t\topus, err := m.codec.Encode(frame)\n",
                     "\t\tstarted:=time.Now()\n\t\topus, err := m.codec.Encode(frame)\n"
                     '\t\tm.canObserve(MediaEvent{Kind:"encode", Samples:len(frame), '
                     'Duration:time.Since(started), Failed:err!=nil})\n')
    source = replace(source,
                     "\tpkt := m.rtpSession.CreatePacketWithDuration(opus, "
                     "m.codec.FrameSize(), marker)\n",
                     "\tpkt := m.rtpSession.CreatePacketWithDuration(opus, "
                     "m.codec.FrameSize(), marker)\n"
                     '\tm.canObserve(MediaEvent{Kind:"rtp", Samples:m.codec.FrameSize(), '
                     "Sequence:pkt.Header.SequenceNumber, Timestamp:pkt.Header.Timestamp, "
                     "SSRC:pkt.Header.Ssrc, Marker:marker})\n")
    source = replace(source, "\t\tm.log.Debug(\"srtp protect error\", \"err\", err)\n",
                     '\t\tm.canObserve(MediaEvent{Kind:"protect_error"})\n'
                     '\t\tm.log.Debug("srtp protect error", "err", err)\n')
    source = replace(source, "\tm.relay.Broadcast(srtp)\n",
                     "\tif measured,ok:=m.relay.(interface{ BroadcastMeasured([]byte) "
                     "transport.SendReport }); ok && m.OnMediaEvent!=nil {\n"
                     '\t\tm.canObserve(MediaEvent{Kind:"relay", '
                     'Relays:measured.BroadcastMeasured(srtp)})\n'
                     "\t} else {\n\t\tm.relay.Broadcast(srtp)\n"
                     '\t\tm.canObserve(MediaEvent{Kind:"relay_unmeasured"})\n\t}\n')
    source = replace(source,
                     "\t\t\t\t\tif opus, err := m.codec.Encode(silence); err == nil {\n",
                     "\t\t\t\t\tstarted:=time.Now()\n"
                     "\t\t\t\t\topus,err:=m.codec.Encode(silence)\n"
                     '\t\t\t\t\tm.canObserve(MediaEvent{Kind:"keepalive_encode", '
                     'Samples:len(silence), '
                     "Duration:time.Since(started), Failed:err!=nil})\n"
                     "\t\t\t\t\tif err == nil {\n")
    return source


def transport(source: str) -> str:
    source = replace(source, "\tstopCh     chan struct{}\n",
                     "\tstopCh     chan struct{}\n\tcanMetricSlot uint64\n")
    source = replace(source, "\tonReceive func(data []byte)\n",
                     "\tonReceive func(data []byte)\n\tcanNextMetricSlot uint64\n")
    return replace(source, "\tm.connections[id] = conn\n",
                   "\tm.canNextMetricSlot++\n\tconn.canMetricSlot=m.canNextMetricSlot\n"
                   "\tm.connections[id] = conn\n")


TRANSFORMS = {
    "internal/voip/call/callmanager.go": manager,
    "internal/voip/call/callmanager_media.go": media,
    "internal/voip/transport/sctprelay.go": transport,
}
