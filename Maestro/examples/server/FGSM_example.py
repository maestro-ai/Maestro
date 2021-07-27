import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import datasets, transforms

import sys
import json
import requests
import numpy as np
import base64
import zlib



from Maestro.pipeline import Pipeline, Scenario, AutoPipelineForVision
from Maestro.models.model import build_model
from Maestro.data import get_dataset


class pred_hook:
    def __init__(self, fn):
        self._fn = fn

    def __call__(self, *args, **kwargs):
        return self._fun(*args, **kwargs)

    def as_pickle(self):
        import dill as pickle

        return pickle.dumps(self._fn, protocol=2)


def fgsm_attack(image, epsilon, data_grad):
    sign_data_grad = np.sign(data_grad)
    perturbed_image = image + epsilon * sign_data_grad
    perturbed_image = np.clip(perturbed_image, 0, 1)
    return perturbed_image


def produce_fgsm_hook(image, epsilon, data_grad) -> torch.Tensor:
    # print(type(data_grad))
    data_grad = torch.FloatTensor(data_grad)
    # print("data_grad", data_grad)
    sign_data_grad = data_grad.sign()

    def fgsm_hook(x):
        ## TODO Add something to handle device, maybe a function that returns device of the model on the server
        a = sign_data_grad.to(0)
        # print("-----------------")
        print("ttt",x.mean())
        # print(a)
        perturbed_image = x + epsilon * a
        print(perturbed_image.mean())
        perturbed_image = torch.clamp(perturbed_image, 0, 1)
        return perturbed_image

    return fgsm_hook


def get_output(url, uid, data, label, data_type, hook=None, gradient=False):
    method_class = pred_hook(hook)
    pickled_method = method_class.as_pickle()
    transfer_data = zlib.compress(data)
    transfer_data = base64.b64encode(transfer_data)
    # json_data = np.fromstring(data, dtype=np.float)
    # print(json_data.shape)
    payload = {
        "Application_Name": "FGSM",
        "uids": uid,
        "data_type": data_type,
        "data": transfer_data,
        "label": label,
        "shape": str(data.shape)
    }
    final_url = url + "/get_batch_output"
    if gradient:
        final_url = url + "/get_batch_input_gradient"
    # print(payload)
    response = requests.post(
        final_url, data=payload, files={"file": ("holder", pickled_method)}
    )
    # print(response.text)
    returned = response.json()["outputs"]
    # returned = base64.b64decode(returned.encode())
    # returned = zlib.decompress(returned)
    # returned = np.frombuffer(returned)

    if gradient:
        return np.array(json.loads(response.json()["outputs"]))

        # returned = json.loads(response.text)
        # # print(returned["outputs"])
        # return returned["outputs"]
    # print(response.json())
    # return returned
    return json.loads(response.json()["outputs"])


def test(url, device, epsilon):
    # Accuracy counter
    correct = 0
    adv_examples = []
    print("start testing")
    # Loop over all examples in test set
    identify_func = lambda x: x
    data = {"Application_Name": "FGSM", "data_type": "test"}
    final_url = "{0}/get_data".format(url)
    response = requests.post(final_url, data=data)
    retruned_json = response.json()["data"]
    # print(retruned_json[0]["label"])
    test_loader = retruned_json

    for data in test_loader:
        uid = int(data["uid"])
        print("Attacking data id ", uid)
        target = data["label"]
        # data = np.array(data["image"]).tostring()
        img = np.array(data["image"])#.tostring()
        # json_data = np.frombuffer(bytes.fromhex(img), dtype=np.float32)
        # json_data = np.fromstring(img, dtype=np.float)
        # data = data.to(device)
        output = get_output(url, uid, img, target, "test", hook=identify_func, gradient=False)

        init_pred = np.argmax(output)
        if init_pred != target:
            continue
        img_grad = get_output(url, uid, img, target, "test", hook=identify_func, gradient=True)
        # Call FGSM Attack
        perturbed_img = fgsm_attack(img, epsilon, img_grad)

        # Re-classify the perturbed image
        # print("type:", type(img_grad), type(img))
        # print(img.shape)
        # img_grad = img_grad.tolist()
        # print(np.array(img_grad).shape)
        # exit()

        # fgsm_hook = produce_fgsm_hook(img, epsilon, img_grad)
        output = get_output(url, uid, perturbed_img, target, "test", hook=identify_func, gradient=False)
        # print(output)
        final_pred = np.argmax(output)
        print(init_pred, target, final_pred)

        if final_pred == target:
            correct += 1
            # Special case for saving 0 epsilon examples
        #     if (epsilon == 0) and (len(adv_examples) < 5):
        #         adv_ex = perturbed_data.squeeze().detach().cpu().numpy()
        #         adv_examples.append((init_pred.item(), final_pred.item(), adv_ex))
        # else:
        #     # Save some adv examples for visualization later
        #     if len(adv_examples) < 5:
        #         adv_ex = perturbed_data.squeeze().detach().cpu().numpy()
        #         adv_examples.append((init_pred.item(), final_pred.item(), adv_ex))

    # Calculate final accuracy for this epsilon
    final_acc = correct / float(len(test_loader))
    print(
        "Epsilon: {}\tTest Accuracy = {} / {} = {}".format(
            epsilon, correct, len(test_loader), final_acc
        )
    )

    # Return the accuracy and an adversarial example
    return final_acc, adv_examples


def main():
    url = "http://127.0.0.1:5000"
    device = torch.device("cuda:0" if (torch.cuda.is_available()) else "cpu")
    test(url, device, 0.1)


if __name__ == "__main__":
    main()
