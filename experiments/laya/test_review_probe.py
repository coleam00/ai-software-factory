import unittest
import review_probe as probe


class ReviewProbeTests(unittest.TestCase):
    def test_rejects_invalid_or_uncertain_answers(self):
        for probs in ({'neither': .95},
                      {'neither': float('nan'), 'errors': 0, 'docs': 0, 'both': 0},
                      {'neither': .1, 'errors': .7, 'docs': .1, 'both': .1}):
            with self.assertRaises(ValueError):
                probe.read_prediction({'choice': 'neither', 'probabilities': probs})

    def test_low_confidence_is_recorded_as_requiring_fallback(self):
        result = probe.read_prediction({'choice': 'both', 'probabilities': {
            'neither': .1, 'errors': .2, 'docs': .2, 'both': .5}})
        self.assertTrue(result['errors'])
        self.assertTrue(result['docs'])
        self.assertTrue(result['requires_fallback'])

    def test_full_diff_and_body_are_in_model_state(self):
        state = probe.model_state({'title': 'a title', 'body': 'full body'}, 'first\nlast')
        self.assertIn('full body', state)
        self.assertTrue(state.endswith('first\nlast'))

    def test_branch_change_during_capture_is_rejected(self):
        before = {'headRefOid': 'a', 'baseRefOid': 'b', 'title': 't', 'body': 'body'}
        for field in before:
            with self.assertRaises(ValueError):
                probe.check_head(before, {**before, field: 'changed'})


if __name__ == '__main__':
    unittest.main()
