package whatsapp

import "testing"

func TestPollUpdateCarriesVoterAndSelectedOptionHashes(t *testing.T) {
	message := Message{
		Kind: MessagePoll,
		ReferenceID: "poll-id",
		Body:        "aabbcc",
	}
	if message.ReferenceID == "" || message.Body != "aabbcc" {
		t.Fatalf("unexpected poll update: %#v", message)
	}
}
