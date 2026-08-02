package whatsapp

import (
	"os"
	"strings"
	"testing"
)

func TestIncomingPTTPreservesOriginalPayload(t *testing.T) {
	sourceBytes, err := os.ReadFile("event.go")
	if err != nil {
		t.Fatalf("read event.go: %v", err)
	}
	source := string(sourceBytes)
	start := strings.Index(source, "func getMessageAttachments(")
	if start < 0 {
		t.Fatal("getMessageAttachments not found")
	}
	end := strings.Index(source[start:], "\n}\n")
	if end < 0 {
		t.Fatal("end of getMessageAttachments not found")
	}
	function := source[start : start+end]

	required := []string{
		"Preserve incoming WhatsApp voice notes as their original Ogg/Opus payload.",
		"a.MIME = msg.GetMimetype()",
		"a.Data = data",
	}
	for _, fragment := range required {
		if !strings.Contains(function, fragment) {
			t.Errorf("required fragment missing: %s", fragment)
		}
	}

	forbidden := []string{
		"convertSpec",
		"media.Convert(ctx, a.Data",
		"failed to convert incoming attachment",
	}
	for _, fragment := range forbidden {
		if strings.Contains(function, fragment) {
			t.Errorf("incoming conversion fragment still present: %s", fragment)
		}
	}
}
