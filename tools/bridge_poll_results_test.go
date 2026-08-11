package whatsapp

import "testing"

func TestPollUpdateCarriesVoterAndSelectedOptionHashes(t *testing.T) {
	message := Message{
		Kind: MessagePollUpdate,
		PollUpdate: PollUpdate{
			PollID:       "poll-id",
			Voter:        "123@s.whatsapp.net",
			VoterLID:     "456@lid",
			OptionHashes: []string{"aabbcc"},
		},
	}
	if message.PollUpdate.PollID == "" || len(message.PollUpdate.OptionHashes) != 1 {
		t.Fatalf("unexpected poll update: %#v", message.PollUpdate)
	}
}
